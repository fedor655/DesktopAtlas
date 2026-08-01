# -*- coding: utf-8 -*-
"""
Шаг 3. Локальный сервер вьювера.

Отдаёт viewer/index.html и data/map.json, плюс один эндпоинт /api/open,
который реально открывает папку или файл в проводнике.

Открывать можно ТОЛЬКО то, что есть в map.json, и только если путь
физически лежит внутри папок рабочего стола — никакого произвольного
имени с улицы. Слушает исключительно 127.0.0.1.

    python serve.py            → http://127.0.0.1:8777
"""

import os
import sys
import json
import time
import threading
import subprocess
import webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8777"))

MAP_PATH = os.path.join(HERE, "data", "map.json")
DESC_PATH = os.path.join(HERE, "data", "descriptions.json")
if not os.path.exists(MAP_PATH):
    print("Нет data/map.json — сначала: python scan_desktop.py && python build_map.py")
    sys.exit(1)

MAP = json.load(open(MAP_PATH, encoding="utf-8"))

# --- состояние пересчёта ---
rebuild = {"state": "idle", "step": "", "log": "", "finished_at": ""}
rebuild_lock = threading.Lock()


def reload_map():
    """Перечитывает карту с диска после пересчёта."""
    global MAP, ALLOWED
    MAP = json.load(open(MAP_PATH, encoding="utf-8"))
    ALLOWED = build_allowed(MAP)


def run_rebuild():
    """
    Пересканировать стол и пересчитать карту.

    Запускается тем же интерпретатором, что и сервер, — чтобы наверняка
    попасть в окружение с sentence-transformers и umap. Первый прогон долгий,
    повторные быстрые: эмбеддинги неизменившихся элементов берутся из кэша.
    """
    steps = [("Сканирую рабочий стол", "scan_desktop.py"),
             ("Считаю эмбеддинги и карту", "build_map.py")]
    try:
        out = []
        for title, script in steps:
            with rebuild_lock:
                rebuild["step"] = title
            r = subprocess.run([sys.executable, os.path.join(HERE, script)],
                               capture_output=True, cwd=HERE,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            out.append(r.stdout.decode("utf-8", "replace")[-4000:])
            if r.returncode != 0:
                err = r.stderr.decode("utf-8", "replace")[-2000:]
                with rebuild_lock:
                    rebuild.update(state="failed", step=f"{title}: ошибка",
                                   log="\n".join(out) + "\n" + err)
                return
        reload_map()
        with rebuild_lock:
            rebuild.update(state="done", step="Готово", log="\n".join(out),
                           finished_at=time.strftime("%H:%M:%S"))
    except Exception as e:
        with rebuild_lock:
            rebuild.update(state="failed", step=str(e))

DESKTOP = os.path.realpath(MAP["desktop"])
PUBLIC = os.path.realpath(os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop"))
ALLOWED_ROOTS = [DESKTOP, PUBLIC]

def build_allowed(m):
    """имя -> реальный путь, только для того, что есть в карте и лежит на столе"""
    out = {}
    for name, meta in m["meta"].items():
        p = os.path.realpath(meta["path"])
        for root in ALLOWED_ROOTS:
            if os.path.exists(root) and os.path.commonpath([p, root]) == root:
                out[name] = p
                break
    return out


ALLOWED = build_allowed(MAP)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # наружу отдаём только вьювер и саму карту: в data/ лежат ещё сырые сниппеты
    # со всего рабочего стола, им в HTTP делать нечего
    PUBLIC_FILES = {"/data/map.json", "/data/descriptions.json"}

    def do_GET(self):
        # путь без ?query — иначе "/?3d" не совпадёт ни с одним маршрутом
        # и запрос уедет в листинг каталога
        path = self.path.split("?", 1)[0].split("#", 1)[0]

        if path == "/api/rebuild/status":
            with rebuild_lock:
                return self._json(200, dict(rebuild, log=rebuild["log"][-1500:]))

        if path in ("/", "/index.html", "/viewer/", "/viewer/index.html"):
            self.path = "/viewer/index.html"
            return super().do_GET()

        if path in self.PUBLIC_FILES:
            self.path = path
            return super().do_GET()

        candidate = os.path.normpath(os.path.join(HERE, "viewer", path.lstrip("/")))
        viewer_dir = os.path.join(HERE, "viewer")
        if candidate.startswith(viewer_dir) and os.path.isfile(candidate):
            self.path = "/viewer" + path
            return super().do_GET()

        return self._json(404, {"ok": False, "error": "нет такого файла"})

    def do_POST(self):
        if self.path not in ("/api/open", "/api/describe", "/api/rebuild"):
            return self._json(404, {"ok": False, "error": "нет такого метода"})

        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"ok": False, "error": "битый запрос"})

        if self.path == "/api/rebuild":
            with rebuild_lock:
                if rebuild["state"] == "running":
                    return self._json(409, {"ok": False, "error": "уже считается"})
                rebuild.update(state="running", step="Запускаю", log="")
            threading.Thread(target=run_rebuild, daemon=True).start()
            return self._json(200, {"ok": True})

        if self.path == "/api/describe":
            name = req.get("name", "")
            text = (req.get("description") or "").strip()[:1200]
            if name not in MAP["meta"]:
                return self._json(403, {"ok": False, "error": "нет в карте"})
            try:
                blob = json.load(open(DESC_PATH, encoding="utf-8"))
                blob["descriptions"][name] = text
                with open(DESC_PATH, "w", encoding="utf-8") as f:
                    json.dump(blob, f, ensure_ascii=False, indent=1)
                MAP["meta"][name]["description"] = text
                print(f"описание обновлено: {name}")
                return self._json(200, {"ok": True})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})

        name = req.get("name", "")
        path = ALLOWED.get(name)
        if not path:
            return self._json(403, {"ok": False, "error": "нет в карте рабочего стола"})
        if not os.path.exists(path):
            return self._json(404, {"ok": False, "error": "файл исчез с диска"})

        try:
            os.startfile(path)        # noqa: S606 — обычное открытие в проводнике
            print(f"открыл: {name}")
            return self._json(200, {"ok": True, "path": path})
        except OSError as e:
            return self._json(500, {"ok": False, "error": str(e)})

    def log_message(self, *a):
        pass                          # не засоряем консоль


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"Карта смыслов рабочего стола: {url}")
    print(f"Элементов в карте: {len(MAP['meta'])}, открыть можно: {len(ALLOWED)}")
    print("Ctrl+C — остановить")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлен")


if __name__ == "__main__":
    main()
