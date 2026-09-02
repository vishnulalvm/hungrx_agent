class AppError(Exception):
    """Base class for application errors that map to a specific HTTP status
    and a stable machine-readable error code, so handlers never have to
    string-match exception messages."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationAppError(AppError):
    status_code = 422
    code = "validation_error"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"
