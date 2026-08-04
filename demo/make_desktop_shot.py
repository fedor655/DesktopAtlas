# -*- coding: utf-8 -*-
"""
Снимок настоящего рабочего стола с демонстрационными папками.

Показать раскладку на живом рабочем столе, не показав при этом чужие настоящие
папки, — задача с подвохом. Переключить системный путь рабочего стола нельзя:
если он перенесён в OneDrive, тот возвращает его обратно. Переносить настоящие
папки в сторону — трогать десятки гигабайт ради картинки.

Поэтому делаем иначе, не сдвинув ни одного настоящего файла:
  1. кладём на стол демо-папки из demo/desktop;
  2. настоящим элементам ставим атрибут «скрытый» — проводник их не показывает,
     файлы при этом остаются на месте и не двигаются;
  3. ярлыки из общей папки C:\\Users\\Public\\Desktop прячем так же атрибутом,
     а Корзину — штатным переключателем «показывать значки рабочего стола»
     в реестре (увезти её за край не выйдет: Windows возвращает значки,
     оказавшиеся вне основного монитора, обратно);
  4. раскладываем демо-значки по смыслу и снимаем экран;
  5. возвращаем всё обратно.

Возврат выполняется в блоке finally: даже если что-то упадёт посередине,
атрибуты снимутся, демо-папки удалятся, а раскладка восстановится.

    python demo/make_desktop_shot.py
"""

import os
import sys
import time
import json
import shutil
import ctypes
import subprocess

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

DEMO_SRC = os.path.join(HERE, "desktop")
OUT = os.path.join(ROOT, "docs", "desktop.png")

FILE_ATTRIBUTE_HIDDEN = 0x02
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def desktop_dir():
    from desktop_icons import find_desktop_listview  # noqa: F401
    home = os.path.expanduser("~")
    for c in (os.path.join(home, "OneDrive", "Рабочий стол"),
              os.path.join(home, "OneDrive", "Desktop"),
              os.path.join(home, "Desktop")):
        if os.path.isdir(c):
            return c
    raise RuntimeError("не нашёл папку рабочего стола")


def set_hidden(path, hidden):
    attrs = kernel32.GetFileAttributesW(path)
    if attrs == -1:
        return False
    new = attrs | FILE_ATTRIBUTE_HIDDEN if hidden else attrs & ~FILE_ATTRIBUTE_HIDDEN
    return bool(kernel32.SetFileAttributesW(path, new))


RECYCLE_BIN_CLSID = "{645FF040-5081-101B-9F08-00AA002F954E}"
HIDE_ICONS_KEY = (r"Software\Microsoft\Windows\CurrentVersion\Explorer"
                  r"\HideDesktopIcons\NewStartPanel")


def recycle_bin_hidden(hide):
    """Штатный переключатель «показывать Корзину на рабочем столе»."""
    import winreg
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, HIDE_ICONS_KEY, 0,
                                winreg.KEY_ALL_ACCESS) as k:
            was = None
            try:
                was = winreg.QueryValueEx(k, RECYCLE_BIN_CLSID)[0]
            except FileNotFoundError:
                pass
            if hide:
                winreg.SetValueEx(k, RECYCLE_BIN_CLSID, 0, winreg.REG_DWORD, 1)
            elif was is None:
                try:
                    winreg.DeleteValue(k, RECYCLE_BIN_CLSID)
                except FileNotFoundError:
                    pass
            else:
                winreg.SetValueEx(k, RECYCLE_BIN_CLSID, 0, winreg.REG_DWORD, int(was))
            return was
    except OSError as e:
        print(f"  Корзину переключить не вышло: {e}")
        return None


def refresh_desktop():
    """Просим проводник перечитать содержимое стола."""
    SHCNE_ASSOCCHANGED = 0x08000000
    ctypes.WinDLL("shell32").SHChangeNotify(SHCNE_ASSOCCHANGED, 0, None, None)
    time.sleep(2.5)


def screenshot(path):
    ps = r"""
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public class DS{[DllImport("user32.dll")]public static extern bool SetProcessDPIAware();}'
[DS]::SetProcessDPIAware() | Out-Null
$sh = New-Object -ComObject Shell.Application
$sh.MinimizeAll(); Start-Sleep -Milliseconds 1200
$b = ([System.Windows.Forms.Screen]::AllScreens | Where-Object { $_.Primary }).Bounds
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
$bmp.Save('%OUT%', [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Start-Sleep -Milliseconds 300; $sh.UndoMinimizeALL()
""".replace("%OUT%", path.replace("\\", "\\\\"))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)


def best_band(alien_y, demo_y, cell_h, area_top, area_bottom):
    """
    Самая высокая горизонтальная полоса без чужих значков.

    Ряды с чужими значками режут область на куски; берём тот кусок, где
    демо-значков больше всего.
    """
    cuts = sorted(set(alien_y))
    edges = [area_top]
    for y in cuts:
        edges += [y - 8, y + cell_h + 8]
    edges.append(area_bottom)

    best, best_score = None, 0
    for i in range(len(edges) - 1):
        top, bottom = edges[i], edges[i + 1]
        if bottom - top < cell_h:
            continue
        if any(top - 8 < y < bottom for y in cuts):
            continue
        score = sum(1 for y in demo_y if top <= y < bottom)
        if score > best_score:
            best, best_score = (max(area_top, top), min(area_bottom, bottom)), score
    return best


def crop_band(path, top, bottom):
    """Оставляет из кадра только полосу по вертикали."""
    try:
        from PIL import Image
    except ImportError:
        print("  (PIL нет — кадр не обрезан)")
        return
    im = Image.open(path)
    top = max(0, min(int(top), im.height - 50))
    bottom = max(top + 50, min(int(bottom), im.height))
    im.crop((0, top, im.width, bottom)).save(path)
    print(f"  кадр обрезан до {im.width}x{bottom-top}")


DATA = os.path.join(ROOT, "data")
SWAP = ["items.json", "map.json", "descriptions.json", "layout.json",
        "cache_desc.npz", "cache_content.npz"]


def stash_data():
    """Прячет настоящую карту, чтобы поработать с демонстрационной."""
    saved = []
    for name in SWAP:
        p = os.path.join(DATA, name)
        if os.path.exists(p):
            shutil.copy2(p, p + ".realbak")
            saved.append(p)
    return saved


def unstash_data(saved):
    for p in saved:
        if os.path.exists(p + ".realbak"):
            shutil.move(p + ".realbak", p)


def build_demo_map():
    """Сканирует демо-стол и считает по нему карту."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    shutil.copy2(os.path.join(HERE, "descriptions.json"),
                 os.path.join(DATA, "descriptions.json"))
    for name in ("cache_desc.npz", "cache_content.npz"):
        p = os.path.join(DATA, name)
        if os.path.exists(p):
            os.remove(p)          # кэш от настоящего стола демо не подходит
    subprocess.run([sys.executable, os.path.join(ROOT, "scan_desktop.py"), DEMO_SRC],
                   cwd=ROOT, capture_output=True, env=env)
    print("считаю демо-карту (пара минут)...")
    subprocess.run([sys.executable, os.path.join(ROOT, "build_map.py")],
                   cwd=ROOT, capture_output=True, env=env)


def main():
    from desktop_icons import (find_desktop_listview, read_icons, get_state,
                               set_positions, cmd_apply)

    desk = desktop_dir()
    if not os.path.isdir(DEMO_SRC):
        print("собираю демо-стол")
        subprocess.run([sys.executable, os.path.join(HERE, "make_demo_desktop.py")],
                       capture_output=True,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if not os.path.isdir(DEMO_SRC):
        print("Нет demo/desktop — сначала: python demo/make_demo_desktop.py")
        return 1

    demo_names = os.listdir(DEMO_SRC)
    real_names = [n for n in os.listdir(desk) if n not in demo_names]

    hidden, copied = [], []
    defview, lv = find_desktop_listview()
    st = get_state(defview, lv)
    parked_before = {}
    saved = stash_data()

    try:
        build_demo_map()
        # 1. демо-элементы на стол
        print(f"кладу {len(demo_names)} демо-элементов на стол")
        for n in demo_names:
            dst = os.path.join(desk, n)
            src = os.path.join(DEMO_SRC, n)
            if os.path.exists(dst):
                continue
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            copied.append(dst)

        # 2. настоящие — скрываем, и личные, и общие
        pub = os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop")
        targets = [os.path.join(desk, n) for n in real_names]
        if os.path.isdir(pub):
            targets += [os.path.join(pub, n) for n in os.listdir(pub)
                        if n.lower() != "desktop.ini"]
        print(f"скрываю {len(targets)} настоящих элементов (файлы не двигаются)")
        denied = 0
        for p in targets:
            attrs = kernel32.GetFileAttributesW(p)
            if attrs != -1 and not (attrs & FILE_ATTRIBUTE_HIDDEN):
                if set_hidden(p, True):
                    hidden.append(p)
                else:
                    denied += 1
        if denied:
            print(f"  не удалось скрыть: {denied} (нужны права)")

        # 3. Корзина прячется штатным переключателем
        parked_before["_recycle"] = recycle_bin_hidden(True)
        refresh_desktop()

        # 4. раскладка демо-значков
        print("раскладываю демо-значки по смыслу")
        subprocess.run([sys.executable, os.path.join(ROOT, "layout_desktop.py"),
                        "описание", "--grid"], cwd=ROOT, capture_output=True,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        cmd_apply(os.path.join(ROOT, "data", "layout.json"), quiet=True)
        time.sleep(1.5)

        # 5. Ярлыки из общей папки скрыть не вышло (нужны права администратора),
        #    а переставить их Windows не даёт — возвращает на первое свободное
        #    место сверху. Поэтому не двигаем ничего, а измеряем, где они встали,
        #    и вырезаем из кадра самую высокую горизонтальную полосу, в которой
        #    их нет.
        demo_stems = {os.path.splitext(d)[0] for d in demo_names}
        now = read_icons(lv)
        alien_y = sorted({y for _, n, _, y in now
                          if n not in demo_names
                          and os.path.splitext(n)[0] not in demo_stems})
        demo_y = sorted({y for _, n, _, y in now
                         if n in demo_names or os.path.splitext(n)[0] in demo_stems})
        print(f"  чужих значков на рядах y={alien_y}")

        band = best_band(alien_y, demo_y, st["cell_h"], st["area_y"],
                         st["area_y"] + st["area_h"])
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        screenshot(OUT)
        if band:
            top, bottom = band
            n_in = sum(1 for y in demo_y if top <= y < bottom)
            print(f"  чистая полоса: y {top}..{bottom}, демо-рядов в ней {n_in}")
            crop_band(OUT, top - st["area_y"], bottom - st["area_y"])
        else:
            print("  !! чистой полосы не нашлось, кадр без обрезки")
        print(f"снимок: {OUT}")

    finally:
        print("\n--- возвращаю как было ---")
        for p in hidden:
            set_hidden(p, False)
        print(f"сняты атрибуты скрытия: {len(hidden)}")
        if "_recycle" in parked_before:
            recycle_bin_hidden(False)
            print("Корзина возвращена")
        for p in copied:
            try:
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            except OSError as e:
                print(f"  не удалилось {os.path.basename(p)}: {e}")
        print(f"удалены демо-элементы: {len(copied)}")
        unstash_data(saved)
        print("настоящая карта и раскладка возвращены")
        refresh_desktop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
