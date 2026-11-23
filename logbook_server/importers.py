from __future__ import annotations

import csv
import io
from typing import Any

from openpyxl import load_workbook


def parse_csv_bytes(data: bytes) -> list[list[str]]:
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    return [ [str(c) if c is not None else "" for c in r] for r in rows if any(str(c).strip() for c in r) ]


def parse_xlsx_bytes(data: bytes, sheet: str | None = None) -> tuple[str, list[list[str]]]:
    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    name = sheet if sheet and sheet in wb.sheetnames else wb.sheetnames[0]
    ws = wb[name]
    rows = []
    for row in ws.iter_rows(values_only=True):
        vals = [("" if c is None else str(c)) for c in row]
        if any(v.strip() for v in vals):
            rows.append(vals)
    return name, rows


def auto_headers(rows: list[list[str]]) -> list[str]:
    return [h.strip() for h in (rows[0] if rows else [])]


def rows_to_dicts(rows: list[list[str]], mapping: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """mapping: source header (or index as str) -> target field name."""
    if not rows:
        return []
    headers = auto_headers(rows)
    maps: dict[int, str] = {}
    if mapping:
        # keys may be header names or indices
        lower = {h.lower(): i for i, h in enumerate(headers)}
        for k, dest in mapping.items():
            if not dest:
                continue
            if str(k).isdigit():
                maps[int(k)] = dest
            elif k in headers:
                maps[headers.index(k)] = dest
            elif k.lower() in lower:
                maps[lower[k.lower()]] = dest
    else:
        for i, h in enumerate(headers):
            if h:
                maps[i] = h
    out = []
    for row in rows[1:]:
        rec = {}
        for i, dest in maps.items():
            rec[dest] = row[i] if i < len(row) else ""
        out.append(rec)
    return out
