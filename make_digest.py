# -*- coding: utf-8 -*-
"""Компактная выжимка items.json — чтобы человек (или модель) мог написать описания."""
import json, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_map import redact          # те же маски токенов/паролей/почт
data = json.load(open(os.path.join(HERE, "data", "items.json"), encoding="utf-8"))

lines = []
for it in data["items"]:
    head = f"### {it['name']}  [{it['kind']}, {it.get('size_human','')}, {it.get('n_files',0)} файл(ов), {it['mtime']}]"
    lines.append(head)
    if it.get("target"):
        lines.append(f"  цель: {it['target']}")
    exts = ", ".join(f"{k}×{v}" for k, v in list(it.get("ext_hist", {}).items())[:8])
    if exts:
        lines.append(f"  расширения: {exts}")
    tree = it.get("tree", [])[:9]
    if tree:
        lines.append("  файлы: " + " | ".join(tree))
    for sn in it.get("snippets", [])[:2]:
        lines.append(f"  <{sn['file']}> {redact(sn['text'][:400])[:200]}")
    lines.append("")

out = os.path.join(HERE, "data", "digest.md")
text = "\n".join(lines)
open(out, "w", encoding="utf-8").write(text)
print(f"{len(data['items'])} элементов -> {out} ({len(text)} символов)")
