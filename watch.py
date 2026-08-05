# -*- coding: utf-8 -*-
"""
Сторож раскладки.

Проблема, которую он решает. Когда на рабочем столе появляется новый файл или
папка, проводник вставляет её в свой порядок заполнения и сдвигает всё, что
идёт следом: часть значков уезжает на ряд вниз, часть на два, нижние
перескакивают в соседнюю колонку. Смысловая раскладка после этого — каша.
Автоупорядочивание при этом выключено, и отключить саму переупаковку нечем.

Поэтому раскладка не «ставится», а поддерживается: сторож раз в несколько
секунд сверяет позиции с data/layout.json и, если разъехалось много значков
сразу, кладёт всё обратно.

Осознанные перестановки руками он не трогает: если ты сам перетащил один-два
значка, это остаётся как есть. Возврат срабатывает только на массовый сдвиг —
такой бывает у проводника, а не у человека.

    python watch.py              # следить, пока не остановишь
    python watch.py --once       # проверить один раз и выйти
"""

import os
import sys
import json
import time
from datetime import datetime

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LOG_DIR = os.path.join(HERE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG = os.path.join(LOG_DIR, "watch.log")

POLL_SECONDS = 4
# Сколько значков должно съехать, чтобы считать это переупаковкой проводника,
# а не осознанным перетаскиванием. Человек за раз двигает единицы.
DRIFT_TRIGGER = 6
# Не чаще одного возврата в этот интервал — чтобы не устроить драку с проводником.
COOLDOWN_SECONDS = 20


def log(msg, echo=True):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    if echo:
        print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


LAYOUT_PATH = os.path.join(HERE, "data", "layout.json")


def load_layout():
    if not os.path.exists(LAYOUT_PATH):
        return None, None
    blob = json.load(open(LAYOUT_PATH, encoding="utf-8"))
    return ({r["name"]: (r["x"], r["y"]) for r in blob["positions"]},
            blob.get("geometry", {}))


def fix_spacing(lv, defview, want_w, want_h):
    """
    Возвращает шаг сетки, под который считалась раскладка.

    Без этого сторож бессилен: если Windows сбросила шаг (а она это делает
    при перезапуске проводника и при смене конфигурации мониторов), координаты
    раскладки перестают попадать в узлы её сетки. Каждый возврат округляется
    не туда, и сторож крутится вхолостую до бесконечности.
    """
    from desktop_icons import get_state, user32
    st = get_state(defview, lv)
    if (st["cell_w"], st["cell_h"]) == (want_w, want_h):
        return False
    LVM_SETICONSPACING = 0x1000 + 53
    user32.SendMessageW(lv, LVM_SETICONSPACING, 0,
                        (int(want_h) << 16) | (int(want_w) & 0xFFFF))
    time.sleep(1.0)
    now = get_state(defview, lv)
    log(f"шаг сетки был {st['cell_w']}x{st['cell_h']}, вернул "
        f"{now['cell_w']}x{now['cell_h']}")
    return True


def rebuild_layout():
    """Пересчитывает раскладку под текущую геометрию экранов."""
    import subprocess
    log("геометрия экрана изменилась — пересчитываю раскладку")
    r = subprocess.run([sys.executable, os.path.join(HERE, "layout_desktop.py"),
                        "описание", "--grid"], cwd=HERE, capture_output=True,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if r.returncode != 0:
        log(f"пересчёт не удался: {r.stderr.decode('utf-8', 'replace')[-300:]}")
        return False
    return True


def drift(want, current):
    """Сколько значков из раскладки стоят не там, где должны."""
    off = []
    for name, pos in want.items():
        got = current.get(name) or current.get(os.path.splitext(name)[0])
        if got is not None and got != pos:
            off.append(name)
    return off


LOCK = os.path.join(LOG_DIR, "watch.lock")


def already_running():
    """
    Второй сторож не нужен: два процесса начнут наперегонки возвращать
    раскладку и мешать друг другу. Держим PID в файле и проверяем, жив ли он.
    """
    # Создаём файл атомарно (O_EXCL): если два сторожа стартуют одновременно,
    # выиграет ровно один — обычная проверка «существует ли файл» с последующей
    # записью здесь не годится, между ними успевает влезть второй процесс.
    for _ in range(2):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            return None
        except FileExistsError:
            try:
                pid = int(open(LOCK, encoding="utf-8").read().strip())
            except (OSError, ValueError):
                pid = None
            if pid and pid != os.getpid() and pid_alive(pid):
                return pid
            # замок от процесса, которого уже нет — убираем и пробуем снова
            try:
                os.remove(LOCK)
            except OSError:
                pass
    return None


def pid_alive(pid):
    import ctypes
    PROCESS_QUERY_LIMITED = 0x1000
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
    if not h:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return code.value == 259          # STILL_ACTIVE


def main():
    from desktop_icons import find_desktop_listview, read_icons, cmd_apply

    once = "--once" in sys.argv
    if not once:
        other = already_running()
        if other:
            log(f"сторож уже работает (pid {other}) — выхожу")
            return 0
    want, geom = load_layout()
    if not want:
        log("нет data/layout.json — сначала layout_desktop.py")
        return 1

    layout_path = LAYOUT_PATH
    log(f"сторож запущен: {len(want)} значков под присмотром, "
        f"порог {DRIFT_TRIGGER}, опрос раз в {POLL_SECONDS}с")

    last_fix = 0.0
    known = None
    failures = 0          # сколько раз подряд возврат не сошёлся

    while True:
        try:
            from desktop_icons import get_state
            defview, lv = find_desktop_listview()
            st = get_state(defview, lv)
            current = {n: (x, y) for _, n, x, y in read_icons(lv)}
        except Exception as e:
            log(f"рабочий стол недоступен ({e}), жду")
            time.sleep(POLL_SECONDS * 2)
            continue

        # Сменилась конфигурация экранов — старые координаты больше не годятся,
        # раскладку надо считать заново, а не возвращать.
        if geom and (st["area_w"], st["area_h"]) != (geom.get("area_w"),
                                                     geom.get("area_h")):
            log(f"область экрана была {geom.get('area_w')}x{geom.get('area_h')}, "
                f"стала {st['area_w']}x{st['area_h']}")
            if rebuild_layout():
                want, geom = load_layout()
                failures = 0
                last_fix = 0.0

        # Шаг сетки Windows сбрасывает при перезапуске проводника и при смене
        # мониторов. Пока он не тот, возвращать позиции бесполезно: система
        # округлит их к своим узлам, и сторож будет крутиться вхолостую.
        if geom and geom.get("cell_w"):
            if fix_spacing(lv, defview, geom["cell_w"], geom["cell_h"]):
                time.sleep(1.0)
                current = {n: (x, y) for _, n, x, y in read_icons(lv)}
                last_fix = 0.0

        # появление новых элементов само по себе не повод двигать чужие значки,
        # но о нём стоит знать: в карте их ещё нет
        if known is not None:
            added = set(current) - known
            if added:
                log(f"новое на столе: {', '.join(sorted(added))} "
                    f"(в карте их нет — нажми «пересчитать» во вьювере)")
        known = set(current)

        off = drift(want, current)
        if len(off) >= DRIFT_TRIGGER:
            if time.time() - last_fix < COOLDOWN_SECONDS:
                pass                      # только что чинили, даём осесть
            else:
                log(f"разъехалось {len(off)} значков — возвращаю раскладку")
                try:
                    cmd_apply(layout_path, quiet=True)
                    last_fix = time.time()
                    left = drift(want, {n: (x, y) for _, n, x, y in read_icons(lv)})
                    log(f"после возврата не на месте: {len(left)}")

                    # Если возврат раз за разом не сходится, значит мешает что-то,
                    # чего мы не понимаем. Молотить вхолостую хуже, чем отступить:
                    # уходим в долгую паузу, чтобы не грузить систему и не мешать.
                    if len(left) >= len(off) * 0.8:
                        failures += 1
                        if failures >= 3:
                            log("три попытки подряд без толку — отступаю на 10 минут. "
                                "Похоже, нужна ручная пересборка: "
                                "layout_desktop.py описание --grid")
                            time.sleep(600)
                            failures = 0
                    else:
                        failures = 0
                except Exception as e:
                    log(f"вернуть не вышло: {e}")
        elif off:
            log(f"сдвинуто {len(off)}: {', '.join(off[:3])} — "
                f"похоже на ручную перестановку, не трогаю", echo=False)

        if once:
            return 0
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("сторож остановлен")
    finally:
        try:
            if os.path.exists(LOCK) and \
               open(LOCK, encoding="utf-8").read().strip() == str(os.getpid()):
                os.remove(LOCK)
        except OSError:
            pass
