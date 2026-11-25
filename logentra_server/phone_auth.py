"""Phone-cookie authentication restricted to quick-entry endpoints."""
from hmac import compare_digest, digest
import re
from secrets import token_urlsafe
from urllib.parse import urlsplit

from fastapi import HTTPException

from logentra_server.auth import authenticate, hash_token
from logentra_server.models import SessionToken

PHONE_COOKIE = "logentra_phone"
CSRF_COOKIE = "logentra_phone_csrf"


def csrf_secret(request):
    cookie = request.cookies.get(CSRF_COOKIE, "")
    return cookie if re.fullmatch(r"[A-Za-z0-9_-]{43}", cookie) else token_urlsafe(32)


def csrf_token(secret):
    nonce = token_urlsafe(16)
    signature = digest(secret.encode(), nonce.encode(), "sha256").hex()
    return f"{nonce}.{signature}"


def set_csrf_cookie(response, request, secret):
    response.set_cookie(CSRF_COOKIE, secret, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")


def valid_csrf(sent, secret):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", secret):
        return False
    if not re.fullmatch(r"[A-Za-z0-9_-]{22}\.[a-f0-9]{64}", sent):
        return False
    nonce, signature = sent.split(".")
    return compare_digest(signature, digest(secret.encode(), nonce.encode(), "sha256").hex())


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
    if not same_origin or not valid_csrf(sent, expected):
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
    return authenticate(db, settings, authorization=authorization, x_api_key=api_key,
                        request_path=request.url.path)
