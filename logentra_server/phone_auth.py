"""Phone-cookie authentication restricted to quick-entry endpoints."""
from hmac import compare_digest
from secrets import token_urlsafe
from urllib.parse import urlsplit

from fastapi import HTTPException

from logentra_server.auth import authenticate, hash_token
from logentra_server.models import SessionToken

PHONE_COOKIE = "logentra_phone"
CSRF_COOKIE = "logentra_phone_csrf"


def csrf_token(request):
    return request.cookies.get(CSRF_COOKIE) or token_urlsafe(32)


def set_csrf_cookie(response, request, token):
    response.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")


def require_csrf(request):
    sent = request.headers.get("X-CSRF-Token", "")
    expected = request.cookies.get(CSRF_COOKIE, "")
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    try:
        address = urlsplit(origin)
        same_origin = (address.scheme, address.netloc.lower()) == (
            request.url.scheme, request.url.netloc.lower(),
        )
    except ValueError:
        same_origin = False
    if (not same_origin or not sent or not expected or not sent.isascii()
            or not expected.isascii() or not compare_digest(sent, expected)):
        raise HTTPException(403, "Reload the QR page and try again")


def phone_principal(db, request, settings, *, write=False):
    authorization = request.headers.get("authorization")
    api_key = request.headers.get("x-api-key")
    if not authorization and not api_key:
        if write:
            require_csrf(request)
        raw = request.cookies.get(PHONE_COOKIE)
        if raw:
            row = db.query(SessionToken).filter_by(token_hash=hash_token(raw), kind="phone").one_or_none()
            if not row:
                raise HTTPException(401, "Phone session expired")
            authorization = f"Bearer {raw}"
    return authenticate(db, settings, authorization=authorization, x_api_key=api_key)
