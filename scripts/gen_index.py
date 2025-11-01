#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1] / "items"

def gen_index(dirpath: pathlib.Path):
    entries = []
    for p in sorted(dirpath.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix == ".md" and p.name.lower() != "readme.md":
            title = p.stem
            entries.append(f"- [{title}](./{p.name})")
        elif p.is_dir():
            label = p.name
            if (p / "README.md").exists():
                entries.append(f"- [{label}](./{p.name}/README.md)")
            else:
                entries.append(f"- {label}/")
    title = dirpath.name if dirpath.name != "items" else "物品索引"
    content = "# " + title + "\n\n" + "\n".join(entries) + "\n"
    (dirpath / "README.md").write_text(content, encoding="utf-8")

def walk(dirpath: pathlib.Path):
    gen_index(dirpath)
    for p in dirpath.iterdir():
        if p.is_dir():
            walk(p)

if __name__ == "__main__":
    walk(ROOT)
