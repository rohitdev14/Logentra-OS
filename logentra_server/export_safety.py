"""Keep exported spreadsheet text from being interpreted as a formula."""

import math
import re

PLAIN_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", re.ASCII)


def safe_cell(value):
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    if PLAIN_NUMBER.fullmatch(trimmed) and math.isfinite(float(trimmed)):
        return value
    if value.startswith(("\t", "\r")) or trimmed.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
