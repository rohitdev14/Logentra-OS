#!/usr/bin/env python3
"""Assemble the self-contained Logbook HTML file from ui/src."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from logbook_server.branding import APP_VERSION, PRODUCT_NAME, PRODUCT_TAGLINE, RELEASE_LABEL

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
    v4 = (SRC / "v4.js").read_text(encoding="utf-8")
    theme = (SRC / "theme.js").read_text(encoding="utf-8")
    v5 = (SRC / "v5.js").read_text(encoding="utf-8")
    shell = (SRC / "shell.html").read_text(encoding="utf-8")
    html = (
        shell.replace("__PRODUCT_NAME__", PRODUCT_NAME)
        .replace("__PRODUCT_TAGLINE__", PRODUCT_TAGLINE)
        .replace("__APP_VERSION__", APP_VERSION)
        .replace("__RELEASE_LABEL__", RELEASE_LABEL)
        .replace("__CSS__", css)
        .replace("__THEME__", theme)
        .replace("__XLSX__", xlsx)
        .replace("__PACKS__", packs_js)
        .replace("__API__", api)
        .replace("__JS__", js)
        .replace("__V4__", v4)
        .replace("__V5__", v5)
    )
    OUT.write_text(html, encoding="utf-8")
    OUT2.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT} and {OUT2} ({OUT.stat().st_size} bytes), packs={len(packs)}")


if __name__ == "__main__":
    main()
