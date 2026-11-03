"""What an operator needs to see and what a browser should be told, on every response.

* A request id (`X-Request-ID`, kept from the caller when sane, made otherwise) is returned and
  put in the log line, so a user's "it failed" can be matched to the server's record.
* One log line per request: method, path (never the query string, which can carry filters or
  secrets), status and milliseconds. Bodies and headers are never logged. A request that ends in an
  unhandled error is logged with its traceback and answered with a plain 500 that names only the
  request id.
* Security headers on every response; the API is never cached by a browser or proxy.
"""

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from heyyerp_api.config import get_settings

log = logging.getLogger("heyyerp.request")
SANE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def configure_logging() -> None:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s")


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def observe(request: Request,
                      call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        supplied = request.headers.get("x-request-id", "")
        rid = supplied if SANE_ID.match(supplied) else uuid.uuid4().hex
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("%s %s rid=%s unhandled error", request.method, request.url.path, rid)
            response = JSONResponse(
                {"detail": "Something went wrong on the server", "request_id": rid},
                status_code=500)
        ms = round((time.perf_counter() - started) * 1000)
        log.info("%s %s -> %s %sms rid=%s", request.method, request.url.path,
                 response.status_code, ms, rid)
        response.headers["X-Request-ID"] = rid
        for name, value in HEADERS.items():
            response.headers.setdefault(name, value)
        if get_settings().app_env == "production":
            response.headers.setdefault("Strict-Transport-Security",
                                        "max-age=31536000; includeSubDomains")
        return response
