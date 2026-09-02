import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("hungrx.access")

REQUEST_ID_HEADER = "X-Request-ID"

# An inbound request id is client-supplied and echoed straight back into
# structured logs — capped and charset-restricted so a malicious or
# malformed value can't bloat log storage or smuggle unexpected
# characters into log correlation; a value outside these bounds is
# simply replaced with a fresh generated id rather than accepted as-is.
_MAX_REQUEST_ID_LENGTH = 128
_ALLOWED_REQUEST_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
)


def _sanitize_request_id(raw_value: str | None) -> str:
    if (
        raw_value
        and 0 < len(raw_value) <= _MAX_REQUEST_ID_LENGTH
        and all(char in _ALLOWED_REQUEST_ID_CHARS for char in raw_value)
    ):
        return raw_value
    return str(uuid.uuid4())


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request ID (reusing an inbound one if present and
    well-formed) and logs one structured access line per request. The
    request ID is echoed back on the response and attached to
    request.state so error handlers and route handlers can include it in
    logs/error envelopes."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _sanitize_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        response.headers[REQUEST_ID_HEADER] = request_id

        logger.info(
            "%s %s -> %s (%sms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
