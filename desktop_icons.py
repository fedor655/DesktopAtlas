# -*- coding: utf-8 -*-
"""
Шаг 4. Чтение и расстановка РЕАЛЬНЫХ иконок рабочего стола Windows.

Рабочий стол — это обычный ListView (SysListView32) внутри explorer.exe.
Значит позицию каждой иконки можно спросить и задать оконными сообщениями.
Так как окно чужого процесса, структуры приходится класть в его память
через VirtualAllocEx / ReadProcessMemory.

Команды:
    python desktop_icons.py info                 — что за окно, сколько иконок, сетка/автоупорядочивание
    python desktop_icons.py backup               — сохранить текущие позиции в backups/<дата>.json
    python desktop_icons.py apply <layout.json>  — расставить по раскладке (сначала делает бекап)
    python desktop_icons.py restore <файл.json>  — вернуть позиции из бекапа
    python desktop_icons.py freeform on|off      — выключить/включить "упорядочить автоматически" и "выровнять по сетке"
    python desktop_icons.py spacing <cx> <cy>    — шаг сетки значков в px (до перезапуска проводника)
    python desktop_icons.py iconsize [px]        — настоящий размер значков: без аргумента показать, с аргументом сменить
    python desktop_icons.py restart-explorer     — перезапустить проводник, чтобы размер применился
    python desktop_icons.py autostart install    — восстанавливать сетку, раскладку и сервер при входе в систему
    python desktop_icons.py autostart remove     — снять автозапуск
    python desktop_icons.py autostart status     — проверить, стоит ли

Ничего не перемещает и не удаляет на диске — меняются только координаты иконок.
"""

import os
import sys
import json
import time
import ctypes
import ctypes.wintypes as w
from datetime import datetime

# Под pythonw.exe консоли нет и sys.stdout == None — без этой проверки
# reconfigure падает прямо на импорте, причём молча.
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUPS = os.path.join(HERE, "backups")
os.makedirs(BACKUPS, exist_ok=True)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Без этого Windows врёт нам про размеры окон при масштабе экрана != 100%,
# а координаты значков приходят в настоящих пикселях — и всё разъезжается.
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
except Exception:
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except Exception:
        user32.SetProcessDPIAware()

LVM_FIRST = 0x1000
LVM_GETITEMCOUNT = LVM_FIRST + 4
LVM_GETITEMPOSITION = LVM_FIRST + 16
LVM_SETITEMPOSITION32 = LVM_FIRST + 49
LVM_GETITEMTEXTW = LVM_FIRST + 115
LVM_GETITEMSPACING = LVM_FIRST + 51
LVM_REDRAWITEMS = LVM_FIRST + 21
LVM_GETEXTENDEDLISTVIEWSTYLE = LVM_FIRST + 55
LVM_SETICONSPACING = LVM_FIRST + 53

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
MONITORINFOF_PRIMARY = 1

LVIF_TEXT = 0x0001
LVS_AUTOARRANGE = 0x0100
LVS_EX_SNAPTOGRID = 0x00080000
GWL_STYLE = -16

WM_COMMAND = 0x0111
DEFVIEW_AUTOARRANGE = 0x7041      # пункт "Упорядочить значки автоматически"
DEFVIEW_ALIGNTOGRID = 0x7042      # пункт "Выровнять значки по сетке"

PROCESS_ALL = 0x000F0000 | 0x00100000 | 0xFFFF
MEM_COMMIT_RESERVE = 0x1000 | 0x2000
PAGE_READWRITE = 0x04
MEM_RELEASE = 0x8000


class LVITEMW(ctypes.Structure):
    _fields_ = [
        ("mask", w.UINT), ("iItem", ctypes.c_int), ("iSubItem", ctypes.c_int),
        ("state", w.UINT), ("stateMask", w.UINT),
        ("pszText", ctypes.c_void_p), ("cchTextMax", ctypes.c_int),
        ("iImage", ctypes.c_int), ("lParam", ctypes.c_ssize_t),
        ("iIndent", ctypes.c_int), ("iGroupId", ctypes.c_int),
        ("cColumns", w.UINT), ("puColumns", ctypes.c_void_p),
        ("piColFmt", ctypes.c_void_p), ("iGroup", ctypes.c_int),
    ]


user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [w.HWND, w.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.GetWindowLongW.restype = ctypes.c_long
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualAllocEx.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                    w.DWORD, w.DWORD]
kernel32.VirtualFreeEx.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, w.DWORD]
kernel32.ReadProcessMemory.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.WriteProcessMemory.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]


def find_desktop_listview():
    """
    Обычно: Progman > SHELLDLL_DefView > SysListView32.
    Если включены слайд-шоу обоев / несколько мониторов, DefView уезжает
    в один из служебных WorkerW — тогда ищем перебором верхнеуровневых окон.
    """
    progman = user32.FindWindowW("Progman", None)
    defview = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None) if progman else None

    if not defview:
        found = []

        @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        def enum_proc(hwnd, _):
            child = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
            if child:
                found.append(child)
                return False
            return True

        user32.EnumWindows(enum_proc, 0)
        defview = found[0] if found else None

    if not defview:
        raise RuntimeError("Не найден SHELLDLL_DefView — рабочий стол недоступен")

    lv = user32.FindWindowExW(defview, None, "SysListView32", None)
    if not lv:
        raise RuntimeError("Не найден SysListView32 внутри SHELLDLL_DefView")
    return defview, lv


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT),
                ("rcWork", w.RECT), ("dwFlags", w.DWORD)]


def primary_area():
    """
    Рабочая область ОСНОВНОГО монитора в координатах ListView рабочего стола.

    ListView один на все мониторы и отсчитывается от левого верхнего угла
    виртуального экрана. Если слева от основного стоит ещё монитор, начало
    виртуального экрана отрицательное — и координата 0 попадает не туда,
    куда кажется. Поэтому переводим: listview = screen - начало виртуального.
    """
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)

    found = {}

    @ctypes.WINFUNCTYPE(w.BOOL, w.HANDLE, w.HDC, ctypes.POINTER(w.RECT), w.LPARAM)
    def cb(hmon, hdc, lprc, lp):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        if mi.dwFlags & MONITORINFOF_PRIMARY:
            found["r"] = mi.rcWork
        return True

    user32.EnumDisplayMonitors(None, None, cb, 0)
    r = found.get("r")
    if r is None:
        return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return r.left - vx, r.top - vy, r.right - r.left, r.bottom - r.top


class RemoteBuffer:
    """Кусок памяти в чужом процессе + удобные read/write."""

    def __init__(self, hwnd, size=4096):
        pid = w.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        self.pid = pid.value
        self.proc = kernel32.OpenProcess(PROCESS_ALL, False, self.pid)
        if not self.proc:
            raise RuntimeError(f"OpenProcess({self.pid}) не удался — "
                               f"запусти скрипт от того же пользователя, что и explorer")
        self.addr = kernel32.VirtualAllocEx(self.proc, None, size,
                                            MEM_COMMIT_RESERVE, PAGE_READWRITE)
        if not self.addr:
            raise RuntimeError("VirtualAllocEx не удался")
        self.size = size

    def write(self, offset, data):
        n = ctypes.c_size_t(0)
        kernel32.WriteProcessMemory(self.proc, ctypes.c_void_p(self.addr + offset),
                                    ctypes.byref(data), ctypes.sizeof(data),
                                    ctypes.byref(n))

    def read(self, offset, ctype):
        buf = ctype()
        n = ctypes.c_size_t(0)
        kernel32.ReadProcessMemory(self.proc, ctypes.c_void_p(self.addr + offset),
                                   ctypes.byref(buf), ctypes.sizeof(buf), ctypes.byref(n))
        return buf

    def close(self):
        if self.addr:
            kernel32.VirtualFreeEx(self.proc, ctypes.c_void_p(self.addr), 0, MEM_RELEASE)
            self.addr = None
        if self.proc:
            kernel32.CloseHandle(self.proc)
            self.proc = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


TEXT_OFF = 1024          # где в удалённом буфере лежит строка имени
TEXT_CHARS = 512


def read_icons(lv):
    """[(индекс, имя, x, y), ...] — текущее состояние рабочего стола."""
    count = user32.SendMessageW(lv, LVM_GETITEMCOUNT, 0, 0)
    out = []
    with RemoteBuffer(lv) as rb:
        for i in range(count):
            item = LVITEMW()
            item.mask = LVIF_TEXT
            item.iItem = i
            item.iSubItem = 0
            item.pszText = ctypes.c_void_p(rb.addr + TEXT_OFF)
            item.cchTextMax = TEXT_CHARS
            rb.write(0, item)
            user32.SendMessageW(lv, LVM_GETITEMTEXTW, i, rb.addr)
            raw = rb.read(TEXT_OFF, ctypes.c_wchar * TEXT_CHARS)
            name = "".join(raw).split("\x00")[0]

            user32.SendMessageW(lv, LVM_GETITEMPOSITION, i, rb.addr + 512)
            pt = rb.read(512, w.POINT)
            out.append((i, name, pt.x, pt.y))
    return out


def _write(rb, lv, idx, x, y):
    pt = w.POINT(int(x), int(y))
    rb.write(0, pt)
    user32.SendMessageW(lv, LVM_SETITEMPOSITION32, idx, rb.addr)


def set_positions(lv, index_to_xy, defview=None, verbose=False):
    """
    index -> (x, y).

    Тонкость: при включённом «выровнять значки по сетке» (а выключить его
    сообщением извне Windows не даёт) система не кладёт значок в клетку,
    которую прямо сейчас занимает другой, — отпихивает в соседнюю. Значит
    порядок перестановок важен: сначала освободить клетку, потом занимать.

    Раньше это решалось волнами с перечиткой позиций у проводника после
    каждого прохода. Оказалось негодно: отвечает он с задержкой, модель
    занятости расходилась с правдой, и вместо распутывания цепочек
    начиналась толчея — две сотни парковок и десятки непоставленных значков.

    Теперь вся перестановка планируется в памяти по одному снимку позиций.
    Это классическая перестановка: идём по цепочке «кто сидит на моём месте»,
    рекурсивно освобождая её с хвоста. Замкнулась в кольцо — один участник
    уходит на времянку и разрывает круг. Проводнику отдаётся уже готовая
    последовательность ходов, каждый из которых заведомо в свободную клетку.
    """
    st = get_state(defview, lv) if defview else {"cell_w": 88, "cell_h": 116}
    cw, ch = max(8, st["cell_w"]), max(8, st["cell_h"])

    icons = read_icons(lv)
    base_x = min((x for _, _, x, _ in icons), default=0)
    base_y = min((y for _, _, _, y in icons), default=0)

    def cell(x, y):
        """Координаты -> клетка. Сравнивать позиции точным равенством нельзя:
        snap-to-grid подправляет их на пару пикселей."""
        return (round((x - base_x) / cw), round((y - base_y) / ch))

    where = {i: cell(x, y) for i, _, x, y in icons}
    occ = {c: i for i, c in where.items()}
    goal = {i: cell(x, y) for i, (x, y) in index_to_xy.items() if i in where}
    goal_xy = {i: (int(x), int(y)) for i, (x, y) in index_to_xy.items()}
    wanted = set(goal.values())

    rows = st.get("area_h", 1400) // ch + 1
    cols = st.get("area_w", 2560) // cw + 1

    def spare_cell():
        """Свободная клетка, на которую никто не целится. Ищем с конца сетки:
        парковка в правом нижнем углу глаза не мозолит, а в левом верхнем
        сразу бросается — там раньше и скапливались «сошедшие с ума» значки."""
        for r in range(rows - 1, -1, -1):
            for c in range(cols - 1, -1, -1):
                if (c, r) not in occ and (c, r) not in wanted:
                    return (c, r)
        return None

    moves = []
    parked = 0

    def shift(i, c):
        old = where.get(i)
        if old is not None and occ.get(old) == i:
            del occ[old]
        occ[c] = i
        where[i] = c
        xy = goal_xy[i] if goal.get(i) == c else (base_x + c[0] * cw, base_y + c[1] * ch)
        moves.append((i, xy))

    def relocate(i, chain):
        nonlocal parked
        t = goal.get(i)
        if t is None or where.get(i) == t:
            return
        blocker = occ.get(t)
        if blocker is not None and blocker != i:
            if blocker in chain:
                s = spare_cell()
                if s is None:
                    return
                shift(blocker, s)
                parked += 1
            elif blocker in goal:
                relocate(blocker, chain | {i})
                if occ.get(t) not in (None, i):
                    s = spare_cell()
                    if s is None:
                        return
                    shift(occ[t], s)
                    parked += 1
            else:
                s = spare_cell()
                if s is None:
                    return
                shift(blocker, s)
        if occ.get(t) in (None, i):
            shift(i, t)

    sys.setrecursionlimit(max(3000, len(goal) * 6))
    for i in list(goal):
        try:
            relocate(i, frozenset())
        except RecursionError:
            break

    with RemoteBuffer(lv) as rb:
        for i, (x, y) in moves:
            _write(rb, lv, i, x, y)

    user32.SendMessageW(lv, LVM_REDRAWITEMS, 0,
                        user32.SendMessageW(lv, LVM_GETITEMCOUNT, 0, 0))
    user32.InvalidateRect(lv, None, True)

    left = sum(1 for i, t in goal.items() if where.get(i) != t)
    if verbose:
        print(f"  перестановок: {len(moves)}, временных парковок: {parked}, "
              f"не удалось поставить: {left}")
    return left


def get_state(defview, lv):
    style = user32.GetWindowLongW(lv, GWL_STYLE)
    ex = user32.SendMessageW(lv, LVM_GETEXTENDEDLISTVIEWSTYLE, 0, 0)
    spacing = user32.SendMessageW(lv, LVM_GETITEMSPACING, 0, 0)
    cx, cy = spacing & 0xFFFF, (spacing >> 16) & 0xFFFF
    rect = w.RECT()
    user32.GetWindowRect(lv, ctypes.byref(rect))
    px, py, pw, ph = primary_area()
    return {
        "auto_arrange": bool(style & LVS_AUTOARRANGE),
        "snap_to_grid": bool(ex & LVS_EX_SNAPTOGRID),
        "cell_w": cx, "cell_h": cy,
        "width": rect.right - rect.left, "height": rect.bottom - rect.top,
        # рабочая область основного монитора — только сюда и раскладываем
        "area_x": px, "area_y": py, "area_w": pw, "area_h": ph,
    }


def cmd_info():
    defview, lv = find_desktop_listview()
    st = get_state(defview, lv)
    icons = read_icons(lv)
    print(f"SHELLDLL_DefView=0x{defview:X}  SysListView32=0x{lv:X}")
    print(f"Весь стол (все мониторы): {st['width']}x{st['height']} px")
    print(f"Основной монитор в этих координатах: "
          f"x {st['area_x']}..{st['area_x']+st['area_w']}, "
          f"y {st['area_y']}..{st['area_y']+st['area_h']}")
    print(f"Шаг сетки значков: {st['cell_w']}x{st['cell_h']} px "
          f"=> {st['area_w']//st['cell_w']}x{st['area_h']//st['cell_h']} ячеек на основном")
    print(f"Упорядочить автоматически: {'ВКЛ' if st['auto_arrange'] else 'выкл'}")
    print(f"Выровнять по сетке:        {'ВКЛ' if st['snap_to_grid'] else 'выкл'}")
    if st["auto_arrange"]:
        print("  !! пока включено автоупорядочивание, произвольные координаты не сохранятся")
    print(f"\nИконок: {len(icons)}")
    for i, name, x, y in icons[:12]:
        print(f"  [{i:3}] ({x:5},{y:5})  {name}")
    if len(icons) > 12:
        print(f"  ... ещё {len(icons)-12}")


def cmd_backup(quiet=False):
    defview, lv = find_desktop_listview()
    icons = read_icons(lv)
    st = get_state(defview, lv)
    blob = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "state": st,
        "icons": [{"index": i, "name": n, "x": x, "y": y} for i, n, x, y in icons],
    }
    path = os.path.join(BACKUPS, datetime.now().strftime("icons_%Y-%m-%d_%H-%M-%S.json"))
    json.dump(blob, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    prune_backups()
    if not quiet:
        print(f"Сохранено {len(icons)} позиций -> {path}")
    return path


def prune_backups(keep=40):
    """
    Оставляет только последние снимки позиций.

    Автозапуск делает бекап при каждом входе в систему, так что без чистки
    папка растёт бесконечно. Самый старый файл не трогаем никогда — это
    исходное состояние стола до всех перестановок.
    """
    files = sorted(f for f in os.listdir(BACKUPS) if f.startswith("icons_"))
    if len(files) <= keep:
        return
    for name in files[1:len(files) - keep + 1]:
        try:
            os.remove(os.path.join(BACKUPS, name))
        except OSError:
            pass


def cmd_restore(path):
    blob = json.load(open(path, encoding="utf-8"))
    defview, lv = find_desktop_listview()
    icons = read_icons(lv)
    by_name = {n: i for i, n, _, _ in icons}
    plan, missed = {}, []
    for rec in blob["icons"]:
        idx = by_name.get(rec["name"])
        if idx is None:
            missed.append(rec["name"])
            continue
        plan[idx] = (rec["x"], rec["y"])
    set_positions(lv, plan, defview, verbose=True)
    print(f"Восстановлено {len(plan)} позиций из {os.path.basename(path)}")
    if missed:
        print(f"Не нашлось на рабочем столе ({len(missed)}): {', '.join(missed[:8])}")


def evict_squatters(plan, icons, st, layout):
    """
    Отодвигает значки, которых нет в раскладке, с чужих мест.

    Появился на столе новый файл — Windows сажает его в первую свободную
    ячейку, и та запросто оказывается местом значка из раскладки. Вернуть
    раскладку становится невозможно: двигать чужака никто не пытается,
    цель заблокирована навсегда. Ровно это и происходило — сторож раз за
    разом сообщал «не на месте: 20» и ничего не мог поделать.

    Поэтому перед раскладкой находим таких сквоттеров и переселяем их
    в ячейки, на которые никто не претендует, начиная с конца сетки —
    чтобы временные жильцы копились в дальнем углу, а не лезли в глаза
    в левом верхнем.
    """
    cw, ch = max(8, st["cell_w"]), max(8, st["cell_h"])
    near_x, near_y = cw * 0.6, ch * 0.6
    targets = list(plan.values())

    def hits_target(x, y):
        return any(abs(tx - x) < near_x and abs(ty - y) < near_y for tx, ty in targets)

    squatters = [(i, x, y) for i, n, x, y in icons
                 if i not in plan and hits_target(x, y)]
    if not squatters:
        return plan, 0

    geom = layout.get("geometry", {})
    ox = geom.get("origin_x", st["area_x"] + 13)
    oy = geom.get("origin_y", st["area_y"] + 2)
    cols = max(1, (st["area_x"] + st["area_w"] - ox) // cw)
    rows = max(1, (st["area_y"] + st["area_h"] - oy) // ch)

    busy = [(x, y) for i, n, x, y in icons if i not in {s[0] for s in squatters}]
    busy += targets

    def is_free(x, y):
        return not any(abs(bx - x) < near_x and abs(by - y) < near_y for bx, by in busy)

    # с конца сетки: правый нижний угол заметно спокойнее левого верхнего
    spare = []
    for r in range(rows - 1, -1, -1):
        for c in range(cols - 1, -1, -1):
            p = (ox + c * cw, oy + r * ch)
            if is_free(*p):
                spare.append(p)
        if len(spare) >= len(squatters):
            break

    for (i, _, _), p in zip(squatters, spare):
        plan[i] = p
        busy.append(p)
    return plan, min(len(squatters), len(spare))


def cmd_apply(layout_path, quiet=False):
    """
    quiet — режим для сторожа: без бекапа и без вывода.

    Бекап перед автоматическим возвратом бессмыслен: раскладка и так лежит
    в layout.json, а сохранять «как было после того, как проводник всё
    перемешал» незачем — именно это состояние мы и чиним.
    """
    layout = json.load(open(layout_path, encoding="utf-8"))
    defview, lv = find_desktop_listview()
    st = get_state(defview, lv)
    if st["auto_arrange"]:
        if not quiet:
            print("Включено 'Упорядочить значки автоматически' — раскладка не удержится.")
            print("Сначала: python desktop_icons.py freeform on")
        return

    if not quiet:
        backup = cmd_backup(quiet=True)
        print(f"Бекап текущих позиций: {os.path.basename(backup)}")

    icons = read_icons(lv)
    # Проводник может прятать расширения, а в самих именах встречаются точки
    # ("LibreOffice 26.2"), поэтому индексируем и по полному имени, и по обрезанному.
    lookup = {}
    for i, n, _, _ in icons:
        lookup.setdefault(os.path.splitext(n)[0], i)
    for i, n, _, _ in icons:
        lookup[n] = i                     # точное совпадение важнее

    plan, missed = {}, []
    for rec in layout["positions"]:
        name = rec["name"]
        idx = lookup.get(name)
        if idx is None:
            idx = lookup.get(os.path.splitext(name)[0])
        if idx is None:
            missed.append(name)
            continue
        plan[idx] = (rec["x"], rec["y"])

    plan, evicted = evict_squatters(plan, icons, st, layout)
    if evicted and not quiet:
        print(f"Отодвинуто чужих значков с занятых мест: {evicted}")

    left = set_positions(lv, plan, defview, verbose=not quiet)
    if quiet:
        return left
    print(f"Расставлено {len(plan)-left} иконок из {len(plan)} "
          f"по раскладке '{layout.get('variant','?')}'")
    if missed:
        print(f"Не нашлось на рабочем столе ({len(missed)}): {', '.join(missed[:8])}")
    print(f"Откат: python desktop_icons.py restore \"{backup}\"")
    return left


def cmd_spacing(cx, cy):
    """
    Меняет шаг сетки значков — сколько места занимает один значок.

    Сам рисунок значка не уменьшается, ужимается только отведённая ему клетка:
    подписи станут у́же и обрежутся сильнее, зато на один монитор влезает
    заметно больше. Windows не даст задать меньше размера самого значка.
    Настройка живёт до перезапуска проводника.
    """
    defview, lv = find_desktop_listview()
    before = get_state(defview, lv)
    user32.SendMessageW(lv, LVM_SETICONSPACING, 0, (cy << 16) | (cx & 0xFFFF))
    after = get_state(defview, lv)
    print(f"Шаг сетки: {before['cell_w']}x{before['cell_h']} -> "
          f"{after['cell_w']}x{after['cell_h']}")
    print(f"Ячеек на основном мониторе: "
          f"{before['area_w']//before['cell_w']}x{before['area_h']//before['cell_h']}"
          f" -> {after['area_w']//after['cell_w']}x{after['area_h']//after['cell_h']}")
    if (after["cell_w"], after["cell_h"]) != (cx, cy):
        print("(Windows подправила значения до минимально допустимых)")


def cmd_iconsize(size=None):
    """
    Показывает, какого размера значки на самом деле.

    Менять размер отсюда нельзя — и это не лень, а результат проверки.
    В реестре есть Shell\\Bags\\1\\Desktop\\IconSize, и записать туда что угодно
    можно, но проводник это значение для рабочего стола не читает: после записи
    32 и перезапуска проводника значки как рисовались в 48 логических пикселей,
    так и продолжили (замерено по скриншоту — глиф папки 64 физических px,
    что соответствует ячейке 72, то есть 48 логических при масштабе 150%).
    Ctrl+колесо, посланное окну сообщением, проводник тоже игнорирует —
    ему нужен настоящий ввод.

    Поэтому размер значков меняется руками: Ctrl+колесо на рабочем столе.
    Это запоминается и переживает перезагрузку. А вот шаг сетки не переживает —
    его и восстанавливает startup.py.
    """
    import winreg

    bags = r"Software\Microsoft\Windows\Shell\Bags\1\Desktop"
    metrics = r"Control Panel\Desktop\WindowMetrics"

    reg_size = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, bags) as k:
            reg_size = winreg.QueryValueEx(k, "IconSize")[0]
    except OSError:
        pass
    spacing = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, metrics) as k:
            for name in ("IconSpacing", "IconVerticalSpacing"):
                try:
                    spacing[name] = winreg.QueryValueEx(k, name)[0]
                except FileNotFoundError:
                    spacing[name] = "(нет)"
    except OSError:
        pass

    dpi = user32.GetDpiForSystem() if hasattr(user32, "GetDpiForSystem") else 96
    scale = dpi / 96
    defview, lv = find_desktop_listview()
    st = get_state(defview, lv)

    print(f"Масштаб экрана: {round(scale*100)}% (DPI {dpi})")
    print(f"Ячейка значка сейчас: {st['cell_w']}x{st['cell_h']} физических px "
          f"= {round(st['cell_w']/scale)}x{round(st['cell_h']/scale)} логических")
    print(f"В реестре IconSize={reg_size}, IconSpacing={spacing.get('IconSpacing')}, "
          f"IconVerticalSpacing={spacing.get('IconVerticalSpacing')}")
    print()
    print("Размер значков меняется только руками: Ctrl+колесо на рабочем столе.")
    print("Записи в реестр проводник для стола игнорирует, проверено.")
    print("Если после этого раскладка разъехалась:")
    print("  python layout_desktop.py описание --grid")
    print("  python desktop_icons.py apply data/layout.json")

    if size is not None:
        print(f"\n(аргумент {size} проигнорирован — см. выше)")


def cmd_restart_explorer():
    """Перезапускает проводник, чтобы подхватились размеры значков."""
    import subprocess
    subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], capture_output=True)
    time.sleep(1.5)
    subprocess.Popen(["explorer.exe"])
    print("Проводник перезапущен. Подожди пару секунд, пока появится рабочий стол.")
    time.sleep(4)


def startup_folder():
    return os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                        "Start Menu", "Programs", "Startup")


AUTOSTART_FILE = "DesktopAtlas.vbs"


def cmd_autostart(action):
    """
    Запуск startup.py при входе в систему через папку «Автозагрузка».

    Планировщик заданий подошёл бы лучше (там есть задержка старта), но
    задачи с триггером «при входе в систему» он создаёт только с правами
    администратора. Папка автозагрузки прав не требует, а задержка нам и не
    нужна: startup.py сам ждёт, пока проводник построит рабочий стол.

    Запускаем через .vbs, а не .bat, чтобы при входе не мелькало чёрное окно
    консоли.
    """
    path = os.path.join(startup_folder(), AUTOSTART_FILE)

    if action == "status":
        if os.path.exists(path):
            print(f"Автозапуск установлен: {path}")
            log_path = os.path.join(HERE, "logs", "startup.log")
            if os.path.exists(log_path):
                tail = open(log_path, encoding="utf-8").read().strip().splitlines()[-4:]
                print("\nПоследние записи журнала:")
                for line in tail:
                    print("  " + line)
        else:
            print("Автозапуск не установлен.")
            print("Поставить:  python desktop_icons.py autostart install")
        return

    if action == "remove":
        if os.path.exists(path):
            os.remove(path)
            print("Автозапуск снят.")
        else:
            print("Автозапуска не было — снимать нечего.")
        return

    if action != "install":
        print("autostart install | remove | status")
        return

    startup = os.path.join(HERE, "startup.py")
    if not os.path.exists(startup):
        print(f"Нет {startup}")
        return

    exe = sys.executable
    quiet = exe.replace("python.exe", "pythonw.exe")
    if os.path.exists(quiet):
        exe = quiet

    vbs = (
        "' Desktop Atlas — восстановление раскладки рабочего стола при входе.\n"
        "' Снять: python desktop_icons.py autostart remove\n"
        'Set sh = CreateObject("WScript.Shell")\n'
        f'sh.CurrentDirectory = "{HERE}"\n'
        f'sh.Run """{exe}"" ""{startup}""", 0, False\n'
    )
    os.makedirs(startup_folder(), exist_ok=True)
    # Именно UTF-16: wscript читает .vbs либо как ANSI, либо как UTF-16 с BOM.
    # В UTF-8 кириллица в пути превратится в мусор, и скрипт молча не запустится.
    with open(path, "w", encoding="utf-16") as f:
        f.write(vbs)

    print(f"Автозапуск установлен: {path}")
    print(f"  запускает: {os.path.basename(exe)} startup.py")
    print("\nПри каждом входе в систему будет:")
    print("  1) возвращён шаг сетки значков (Windows сбрасывает его сама)")
    print("  2) наложена раскладка из data/layout.json")
    print("  3) поднят сервер вьювера на http://127.0.0.1:8777/")
    print("\nНастройки — в data/state.json, журнал — в logs/startup.log")
    print("Проверить сейчас:  python startup.py")
    print("Снять:             python desktop_icons.py autostart remove")


def cmd_freeform(mode):
    defview, lv = find_desktop_listview()
    st = get_state(defview, lv)
    want_auto = mode != "on"      # freeform on  => автоупорядочивание выключаем
    if st["auto_arrange"] != want_auto:
        user32.SendMessageW(defview, WM_COMMAND, DEFVIEW_AUTOARRANGE, 0)
    st = get_state(defview, lv)
    if st["snap_to_grid"] != want_auto:
        user32.SendMessageW(defview, WM_COMMAND, DEFVIEW_ALIGNTOGRID, 0)
    st = get_state(defview, lv)
    print(f"Упорядочить автоматически: {'ВКЛ' if st['auto_arrange'] else 'выкл'}")
    print(f"Выровнять по сетке:        {'ВКЛ' if st['snap_to_grid'] else 'выкл'}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "info":
        cmd_info()
    elif cmd == "backup":
        cmd_backup()
    elif cmd == "restore" and len(sys.argv) > 2:
        cmd_restore(sys.argv[2])
    elif cmd == "apply" and len(sys.argv) > 2:
        cmd_apply(sys.argv[2])
    elif cmd == "freeform" and len(sys.argv) > 2:
        cmd_freeform(sys.argv[2])
    elif cmd == "spacing" and len(sys.argv) > 3:
        cmd_spacing(int(sys.argv[2]), int(sys.argv[3]))
    elif cmd == "iconsize":
        cmd_iconsize(int(sys.argv[2]) if len(sys.argv) > 2 else None)
    elif cmd == "restart-explorer":
        cmd_restart_explorer()
    elif cmd == "autostart":
        cmd_autostart(sys.argv[2] if len(sys.argv) > 2 else "status")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
