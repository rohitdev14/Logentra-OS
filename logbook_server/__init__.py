"""Local server for the Logbook (working title) on-prem package."""

from logbook_server.branding import APP_VERSION as __version__  # noqa: F401
from logbook_server.branding import PRODUCT_NAME, RELEASE_LABEL  # noqa: F401

__all__ = ["__version__", "PRODUCT_NAME", "RELEASE_LABEL"]
