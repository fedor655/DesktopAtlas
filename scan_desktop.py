# -*- coding: utf-8 -*-
"""
Шаг 1. Сканер рабочего стола.

Для каждого элемента (папки или файла) собирает "паспорт":
  - имя, тип, размер, даты
  - для папок: дерево файлов (с ограничениями), гистограмма расширений,
    текстовые сниппеты из README / кода / txt
  - для файлов: содержимое (если текстовое), цель ярлыка (.lnk)

Результат: items.json — вход для шага 2 (эмбеддинги).

Ничего не изменяет на диске, кроме своих выходных файлов.
"""

import os
import io
import sys
import json
import time
import subprocess
from datetime import datetime

# Под pythonw.exe консоли нет и sys.stdout == None — без этой проверки
# reconfigure падает прямо на импорте, причём молча.
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def default_desktop():
    """Личная папка рабочего стола — с учётом переноса в OneDrive."""
    home = os.path.expanduser("~")
    for candidate in (os.path.join(home, "OneDrive", "Рабочий стол"),
                      os.path.join(home, "OneDrive", "Desktop"),
                      os.path.join(home, "Рабочий стол"),
                      os.path.join(home, "Desktop")):
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(home, "Desktop")


# Путь можно передать аргументом: так сканируется демо-стол вместо настоящего.
CUSTOM = len(sys.argv) > 1 and not sys.argv[1].startswith("-")
DESKTOP = os.path.abspath(sys.argv[1]) if CUSTOM else default_desktop()

# Windows показывает на столе объединение личной и общей папок — но только
# для настоящего стола: у произвольной папки никакой «общей части» нет.
PUBLIC_DESKTOP = ("" if CUSTOM else
                  os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop"))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data")
os.makedirs(OUT, exist_ok=True)

# --- что не считаем содержимым проекта (шум зависимостей) ---
SKIP_DIRS = {
    "node_modules", "venv", ".venv", "env", "__pycache__", ".git", ".svn",
    "site-packages", "dist", "build", ".next", "target", ".idea", ".vscode",
    "obj", "bin", "Lib", "Scripts", "Include", ".pytest_cache", ".mypy_cache",
    "vendor", "packages", ".gradle", "cmake-build-debug", "x64", "Debug",
    "Release", ".cache", "coverage", ".terraform", "runtime", "jre",
}
SKIP_FILE_PREFIX = ("~$",)

# расширения, из которых имеет смысл читать текст
TEXT_EXT = {
    ".md", ".txt", ".rst", ".py", ".js", ".mjs", ".ts", ".jsx", ".tsx",
    ".html", ".htm", ".css", ".scss", ".java", ".cpp", ".cc", ".c", ".h",
    ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".bat",
    ".cmd", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".ino", ".kt",
    ".swift", ".sql", ".r", ".lua", ".vue", ".svelte", ".scad", ".gcode",
    ".csv", ".log", ".srt", ".env.example", ".json",
}
# эти файлы читаем в первую очередь — они описывают проект
PRIORITY_NAMES = (
    "readme", "readme.md", "описание", "инструкция", "заметки", "todo",
    "план", "план.txt", "requirements.txt", "package.json", "старт",
    "запуск", "правки", "notes",
)
# json-мусор, который не несёт смысла
JSON_NOISE = {"package-lock.json", "yarn.lock", "tsconfig.json", "composer.lock"}

MAX_WALK_FILES = 4000        # предохранитель на обход одной папки
MAX_DEPTH = 4
MAX_TREE_NAMES = 220         # сколько путей файлов кладём в текст
MAX_SNIPPET_FILES = 22
MAX_SNIPPET_CHARS = 2600     # с одного файла
MAX_CONTENT_CHARS = 45000    # суммарно на элемент


def human_size(n):
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.0f} ТБ"


def read_text(path, limit=MAX_SNIPPET_CHARS):
    """Аккуратное чтение начала текстового файла в любой из ходовых кодировок."""
    try:
        with open(path, "rb") as f:
            raw = f.read(limit * 4)
    except OSError:
        return ""
    if b"\x00" in raw[:1024]:          # похоже на бинарник
        return ""
    for enc in ("utf-8", "utf-8-sig", "cp1251", "cp866"):
        try:
            return raw.decode(enc)[:limit]
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")[:limit]


def snippet_priority(rel, name):
    """Чем меньше число, тем раньше читаем файл."""
    low = name.lower()
    depth = rel.count(os.sep)
    if any(low.startswith(p) for p in PRIORITY_NAMES):
        return (0, depth, len(rel))
    ext = os.path.splitext(low)[1]
    if ext in (".md", ".txt"):
        return (1, depth, len(rel))
    if ext in (".py", ".ino", ".js", ".ts", ".html", ".cs", ".cpp", ".java"):
        return (2, depth, len(rel))
    return (3, depth, len(rel))


def scan_folder(path):
    """Обходит папку проекта и возвращает структурные + текстовые признаки."""
    tree = []          # относительные пути файлов
    ext_hist = {}
    total_size = 0
    n_files = 0
    truncated = False
    candidates = []    # (приоритет, полный путь, rel)

    for root, dirs, files in os.walk(path):
        rel_root = os.path.relpath(root, path)
        depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
        if depth >= MAX_DEPTH:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

        for fn in files:
            if fn.startswith(SKIP_FILE_PREFIX):
                continue
            n_files += 1
            if n_files > MAX_WALK_FILES:
                truncated = True
                break
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, path)
            ext = os.path.splitext(fn)[1].lower()
            ext_hist[ext or "<без расширения>"] = ext_hist.get(ext or "<без расширения>", 0) + 1
            try:
                total_size += os.path.getsize(full)
            except OSError:
                pass
            if len(tree) < MAX_TREE_NAMES:
                tree.append(rel)
            if ext in TEXT_EXT and fn.lower() not in JSON_NOISE:
                candidates.append((snippet_priority(rel, fn), full, rel))
        if n_files > MAX_WALK_FILES:
            truncated = True
            break

    candidates.sort(key=lambda c: c[0])
    snippets = []
    used = 0
    for _, full, rel in candidates[:MAX_SNIPPET_FILES * 3]:
        if len(snippets) >= MAX_SNIPPET_FILES or used >= MAX_CONTENT_CHARS:
            break
        txt = read_text(full)
        txt = " ".join(txt.split())          # схлопываем пробелы/переводы строк
        if len(txt) < 40:
            continue
        txt = txt[: MAX_CONTENT_CHARS - used]
        snippets.append({"file": rel, "text": txt})
        used += len(txt)

    return {
        "n_files": n_files,
        "size": total_size,
        "tree": tree,
        "ext_hist": dict(sorted(ext_hist.items(), key=lambda kv: -kv[1])[:14]),
        "snippets": snippets,
        "truncated": truncated,
    }


def resolve_shortcuts(desktop):
    """Разворачивает все .lnk одним вызовом PowerShell: ярлык -> цель + описание."""
    ps = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$sh=New-Object -ComObject WScript.Shell;"
        f"Get-ChildItem -LiteralPath '{desktop}' -Filter *.lnk | ForEach-Object {{"
        "  $s=$sh.CreateShortcut($_.FullName);"
        "  [PSCustomObject]@{name=$_.Name;target=$s.TargetPath;args=$s.Arguments;desc=$s.Description}"
        "} | ConvertTo-Json -Compress"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=120,
        )
        out = res.stdout.decode("utf-8", errors="replace").strip()
        if not out:
            return {}
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        return {d["name"]: d for d in data if d.get("name")}
    except Exception as e:
        print(f"  [!] не удалось развернуть ярлыки: {e}")
        return {}


def main():
    t0 = time.time()
    print(f"Рабочий стол: {DESKTOP}")
    if not os.path.isdir(DESKTOP):
        print("НЕ НАЙДЕН")
        return

    shortcuts = resolve_shortcuts(DESKTOP)
    if PUBLIC_DESKTOP and os.path.isdir(PUBLIC_DESKTOP):
        shortcuts.update(resolve_shortcuts(PUBLIC_DESKTOP))
    print(f"Ярлыков развёрнуто: {len(shortcuts)}")

    entries = sorted(os.scandir(DESKTOP), key=lambda e: (not e.is_dir(), e.name.lower()))
    if PUBLIC_DESKTOP and os.path.isdir(PUBLIC_DESKTOP):
        entries += sorted(os.scandir(PUBLIC_DESKTOP), key=lambda e: e.name.lower())
    items = []
    seen = set()

    for i, e in enumerate(entries, 1):
        name = e.name
        if name.lower() in ("desktop.ini",) or name in seen:
            continue
        seen.add(name)
        try:
            st = e.stat()
        except OSError:
            continue

        item = {
            "name": name,
            "path": e.path,
            "is_dir": e.is_dir(),
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
            "ctime": datetime.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d"),
        }

        if e.is_dir():
            print(f"[{i}/{len(entries)}] папка: {name}")
            info = scan_folder(e.path)
            item.update(info)
            item["kind"] = "folder"
            item["size_human"] = human_size(info["size"])
        else:
            ext = os.path.splitext(name)[1].lower()
            item["size"] = st.st_size
            item["size_human"] = human_size(st.st_size)
            item["ext"] = ext
            item["n_files"] = 1
            item["tree"] = []
            item["ext_hist"] = {ext: 1}
            item["truncated"] = False
            if ext == ".lnk":
                item["kind"] = "shortcut"
                sc = shortcuts.get(name, {})
                item["target"] = sc.get("target") or ""
                item["target_desc"] = sc.get("desc") or ""
                item["snippets"] = []
                print(f"[{i}/{len(entries)}] ярлык: {name} -> {item['target']}")
            else:
                item["kind"] = "file"
                txt = read_text(e.path, MAX_SNIPPET_CHARS * 4) if ext in TEXT_EXT else ""
                txt = " ".join(txt.split())
                item["snippets"] = [{"file": name, "text": txt}] if len(txt) >= 40 else []
                print(f"[{i}/{len(entries)}] файл: {name}")

        items.append(item)

    out_path = os.path.join(OUT, "items.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {"desktop": DESKTOP,
             "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "items": items},
            f, ensure_ascii=False, indent=1,
        )

    n_dirs = sum(1 for it in items if it["is_dir"])
    print(f"\nГотово за {time.time()-t0:.1f}с: {len(items)} элементов "
          f"({n_dirs} папок, {len(items)-n_dirs} файлов)")
    print(f"-> {out_path}")


if __name__ == "__main__":
    main()
