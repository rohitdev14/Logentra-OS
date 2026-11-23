"""Local server for Logentra OS (on-prem logbook, engineering records, and traceability)."""

from logentra_server.branding import APP_VERSION as __version__  # noqa: F401
from logentra_server.branding import PRODUCT_NAME, PRODUCT_SHORT, RELEASE_LABEL  # noqa: F401

__all__ = ["__version__", "PRODUCT_NAME", "PRODUCT_SHORT", "RELEASE_LABEL"]
