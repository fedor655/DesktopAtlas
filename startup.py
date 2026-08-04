# -*- coding: utf-8 -*-
"""
Что делать при входе в систему.

Зачем это вообще нужно. Размер значка Windows запоминает в реестре и после
перезагрузки честно возвращает. А вот шаг сетки значков — нет: при входе она
пересчитывает его по умолчанию для текущего масштаба экрана, игнорируя
сохранённые IconSpacing/IconVerticalSpacing. Значки разъезжаются на стандартную
сетку, и смысловая раскладка рассыпается.

Поэтому состояние не «настраивается один раз», а переутверждается при каждом
входе: задать шаг сетки, разложить значки по сохранённой раскладке, поднять
сервер вьювера.

Ставится и снимается так:
    python desktop_icons.py autostart install
    python desktop_icons.py autostart remove
    python desktop_icons.py autostart status
"""

import os
import sys
import json
import time
import subprocess
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LOG_DIR = os.path.join(HERE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG = os.path.join(LOG_DIR, "startup.log")

STATE_PATH = os.path.join(HERE, "data", "state.json")
DEFAULT_STATE = {
    "cell_w": 115,
    "cell_h": 123,
    "layout": "data/layout.json",
    "serve": True,
    "port": 8777,
}


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_state():
    state = dict(DEFAULT_STATE)
    if os.path.exists(STATE_PATH):
        try:
            state.update(json.load(open(STATE_PATH, encoding="utf-8")))
        except (OSError, ValueError) as e:
            log(f"state.json не прочитался ({e}), беру значения по умолчанию")
    return state


def wait_for_desktop(timeout=180):
    """
    Ждём, пока проводник построит рабочий стол.

    При входе в систему нас запускают раньше, чем появляется SysListView32,
    а иногда окно есть, но значки в него ещё не загружены — поэтому ждём
    не только окно, но и непустой список.
    """
    from desktop_icons import find_desktop_listview, read_icons

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            defview, lv = find_desktop_listview()
            if len(read_icons(lv)) > 0:
                return defview, lv
        except Exception:
            pass
        time.sleep(2)
    return None, None


def main():
    log("=" * 60)
    log("автозапуск Desktop Atlas")
    state = load_state()

    defview, lv = wait_for_desktop()
    if not lv:
        log("рабочий стол так и не появился — выхожу")
        return 1

    from desktop_icons import get_state, cmd_apply
    import ctypes
    user32 = ctypes.WinDLL("user32")

    before = get_state(defview, lv)
    log(f"рабочий стол готов, шаг сетки {before['cell_w']}x{before['cell_h']}")

    # 1. Шаг сетки. Windows сбрасывает его при входе — возвращаем.
    want = (int(state["cell_w"]), int(state["cell_h"]))
    if (before["cell_w"], before["cell_h"]) != want:
        LVM_SETICONSPACING = 0x1000 + 53
        user32.SendMessageW(lv, LVM_SETICONSPACING, 0,
                            (want[1] << 16) | (want[0] & 0xFFFF))
        after = get_state(defview, lv)
        log(f"шаг сетки: {before['cell_w']}x{before['cell_h']} -> "
            f"{after['cell_w']}x{after['cell_h']}")
        time.sleep(1)          # даём проводнику переразложить значки
    else:
        log("шаг сетки уже правильный")

    # 2. Раскладка значков.
    layout = os.path.join(HERE, state["layout"].replace("/", os.sep))
    if os.path.exists(layout):
        try:
            cmd_apply(layout)
            log(f"раскладка наложена: {os.path.basename(layout)}")
        except Exception as e:
            log(f"раскладку наложить не вышло: {e}")
    else:
        log(f"нет файла раскладки {layout} — пропускаю")

    # 3. Сервер вьювера.
    if state.get("serve", True):
        if port_busy(int(state["port"])):
            log(f"порт {state['port']} уже занят — сервер, видимо, поднят")
        else:
            start_server()

    log("готово")
    return 0


def port_busy(port):
    import socket
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_server():
    """Поднимает serve.py отдельным процессом без консольного окна."""
    exe = sys.executable
    quiet = exe.replace("python.exe", "pythonw.exe")
    if os.path.exists(quiet):
        exe = quiet

    CREATE_NO_WINDOW = 0x08000000
    DETACHED = 0x00000008
    try:
        subprocess.Popen(
            [exe, os.path.join(HERE, "serve.py"), "--no-browser"],
            cwd=HERE,
            creationflags=CREATE_NO_WINDOW | DETACHED,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        log("сервер вьювера запущен")
    except OSError as e:
        log(f"сервер не запустился: {e}")


if __name__ == "__main__":
    sys.exit(main())
