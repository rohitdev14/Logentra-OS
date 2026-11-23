"""Shared pagination helpers for list endpoints."""
from __future__ import annotations

from typing import Any, Sequence

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500


def clamp_page(page: int | None, page_size: int | None) -> tuple[int, int]:
    p = page if page and page > 0 else 1
    size = page_size if page_size and page_size > 0 else DEFAULT_PAGE_SIZE
    return p, min(size, MAX_PAGE_SIZE)


def page_meta(total: int, page: int, page_size: int) -> dict[str, int]:
    pages = (total + page_size - 1) // page_size if page_size else 0
    return {"total": total, "page": page, "page_size": page_size, "pages": pages}


def page_result(items: Sequence[Any], total: int, page: int, page_size: int) -> dict[str, Any]:
    return {"items": list(items), **page_meta(total, page, page_size)}
