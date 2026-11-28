"""Request limits, local-only access and response metadata."""
from __future__ import annotations

import ipaddress
import logging
import math
import re
import time
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.datastructures import Headers, MutableHeaders

log = logging.getLogger("logentra.http")
REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


def loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


LOGIN_WINDOW_SECONDS = 60
LOGIN_TABLE_LIMIT = 4096


class HTTPSecurity:
    def __init__(self, app, settings):
        self.app = app
        self.settings = settings
        # client -> (window start, failed attempts). Only failed sign-ins count;
        # a successful sign-in clears the client's counter.
        self.attempts: dict[str, tuple[float, int]] = {}
        self.trusted = [ipaddress.ip_network(p, strict=False) for p in getattr(settings, "trusted_proxies", []) or []]

    def trusted_peer(self, peer: str) -> bool:
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            return False
        return any(address in network for network in self.trusted)

    def client_key(self, peer: str, headers: Headers) -> str:
        """Identify the real client: the peer, or the proxy-reported address when the peer is trusted."""
        if not self.trusted_peer(peer):
            return peer
        forwarded = [part.strip() for part in headers.get("x-forwarded-for", "").split(",") if part.strip()]
        if not forwarded:
            return peer
        try:
            return str(ipaddress.ip_address(forwarded[-1]))
        except ValueError:
            return peer

    def login_retry_after(self, key: str, now: float) -> int | None:
        window = self.attempts.get(key)
        if window is None:
            return None
        start, count = window
        if now - start >= LOGIN_WINDOW_SECONDS:
            del self.attempts[key]
            return None
        if count < self.settings.login_attempts_per_minute:
            return None
        return max(1, math.ceil(start + LOGIN_WINDOW_SECONDS - now))

    def count_attempt(self, key: str, now: float) -> None:
        """Count a sign-in attempt when it starts, so parallel guesses are limited too."""
        if len(self.attempts) >= LOGIN_TABLE_LIMIT:
            self.attempts = {k: v for k, v in self.attempts.items() if now - v[0] < LOGIN_WINDOW_SECONDS}
        start, count = self.attempts.get(key, (now, 0))
        if now - start >= LOGIN_WINDOW_SECONDS:
            start, count = now, 0
        self.attempts[key] = start, count + 1

    def record_login(self, key: str, status: int) -> None:
        """A successful sign-in clears the counter; only failed attempts add up."""
        if status == 200:
            self.attempts.pop(key, None)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        request_id = headers.get("x-request-id", "")
        if not REQUEST_ID.fullmatch(request_id):
            request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.monotonic()
        peer = (scope.get("client") or ("",))[0]
        forwarded_scheme = headers.get("x-forwarded-proto")
        if forwarded_scheme in {"http", "https"} and self.trusted_peer(peer):
            scope["scheme"] = forwarded_scheme
        is_login = scope["path"].rstrip("/") in {"/api/auth/login", "/q/login"} and scope["method"] == "POST"
        login_key = self.client_key(peer, headers) if is_login else ""

        async def secured_send(message):
            if message["type"] == "http.response.start":
                if is_login:
                    self.record_login(login_key, message["status"])
                response = MutableHeaders(scope=message)
                response["X-Request-ID"] = request_id
                response["X-Content-Type-Options"] = "nosniff"
                response.setdefault("X-Frame-Options", "DENY")
                response["Referrer-Policy"] = "no-referrer"
                if scope["path"].startswith("/api/"):
                    response["Cache-Control"] = "no-store"
                route = getattr(scope.get("route"), "path", "<unmatched>")
                log.info("request method=%s route=%s status=%s request_id=%s duration_ms=%.1f",
                         scope["method"], route, message["status"], request_id,
                         (time.monotonic() - started) * 1000)
            await send(message)

        async def reject(status, detail, extra_headers=None):
            await JSONResponse({"detail": detail}, status, headers=extra_headers)(scope, receive, secured_send)

        if self.settings.auth_mode == "open" and not self.settings.api_key:
            try:
                host = urlsplit("//" + headers.get("host", "")).hostname
                origin = headers.get("origin")
                local_origin = not origin or urlsplit(origin).netloc == headers.get("host")
            except ValueError:
                host, local_origin = None, False
            if not loopback(peer) or not host or not (host == "localhost" or loopback(host)) or not local_origin:
                await reject(403, "Standalone access requires a loopback client")
                return
        if is_login:
            now = time.monotonic()
            retry_after = self.login_retry_after(login_key, now)
            if retry_after is None and login_key not in self.attempts and len(self.attempts) >= LOGIN_TABLE_LIMIT:
                self.attempts = {k: v for k, v in self.attempts.items() if now - v[0] < LOGIN_WINDOW_SECONDS}
                if len(self.attempts) >= LOGIN_TABLE_LIMIT:
                    retry_after = LOGIN_WINDOW_SECONDS
            if retry_after is not None:
                await reject(
                    429,
                    f"Too many sign-in attempts. Please wait {retry_after} seconds and try again.",
                    {"Retry-After": str(retry_after)},
                )
                return
            self.count_attempt(login_key, now)

        limit = self.settings.request_limit_bytes
        length = headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdecimal() or len(length) > 18:
                await reject(400, "Invalid Content-Length")
                return
            if int(length) > limit:
                await reject(413, "Request body too large")
                return

        consumed = 0

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > limit:
                raise HTTPException(413, "Request body too large")
            return message

        await self.app(scope, limited_receive, secured_send)


async def read_upload(file, limit: int) -> bytes:
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "Upload too large")
    if not data:
        raise HTTPException(400, "Upload is empty")
    return data


async def read_request_body(request, limit: int) -> bytearray:
    data = bytearray()
    async for chunk in request.stream():
        if len(chunk) > limit - len(data):
            raise HTTPException(413, "Upload too large")
        data.extend(chunk)
    return data
