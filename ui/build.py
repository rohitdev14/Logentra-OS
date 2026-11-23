#!/usr/bin/env python3
"""Assemble the self-contained Logbook HTML file from ui/src."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "ui" / "src"
OUT = ROOT / "ui" / "Logbook.html"
OUT2 = ROOT / "ui" / "index.html"
PACKS = SRC / "starter_packs.json"


def main() -> None:
    packs = json.loads(PACKS.read_text(encoding="utf-8"))
    packs_js = "window.STARTER_PACKS = " + json.dumps(packs, ensure_ascii=False) + ";\n"
    css = (SRC / "styles.css").read_text(encoding="utf-8")
    xlsx = (SRC / "xlsx.js").read_text(encoding="utf-8")
    api = (SRC / "api.js").read_text(encoding="utf-8")
    js = (SRC / "app.js").read_text(encoding="utf-8")
    shell = (SRC / "shell.html").read_text(encoding="utf-8")
    html = (
        shell.replace("__CSS__", css)
        .replace("__XLSX__", xlsx)
        .replace("__PACKS__", packs_js)
        .replace("__API__", api)
        .replace("__JS__", js)
    )
    OUT.write_text(html, encoding="utf-8")
    OUT2.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT} and {OUT2} ({OUT.stat().st_size} bytes), packs={len(packs)}")


if __name__ == "__main__":
    main()
