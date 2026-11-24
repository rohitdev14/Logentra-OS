"""Request limits, local-only access and response metadata."""
from __future__ import annotations

import ipaddress
import logging
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


class HTTPSecurity:
    def __init__(self, app, settings):
        self.app = app
        self.settings = settings
        self.attempts = {}

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

        async def secured_send(message):
            if message["type"] == "http.response.start":
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

        peer = (scope.get("client") or ("",))[0]
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
        if scope["path"].rstrip("/") == "/api/auth/login" and scope["method"] == "POST":
            minute = int(started // 60)
            if len(self.attempts) >= 4096:
                self.attempts = {key: val for key, val in self.attempts.items() if val[0] == minute}
            window, count = self.attempts.get(peer, (minute, 0))
            count = count if window == minute else 0
            if count >= self.settings.login_attempts_per_minute or (peer not in self.attempts and len(self.attempts) >= 4096):
                await reject(429, "Too many login attempts", {"Retry-After": "60"})
                return
            self.attempts[peer] = minute, count + 1

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
