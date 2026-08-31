from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    field: str | None = None


class ErrorResponse(BaseModel):
    """Uniform error envelope returned by every handled exception.

    Both the admin dashboard and the mobile app parse errors against this
    one shape, regardless of which /api/v1/* module raised them.
    """

    error: ErrorDetail
    request_id: str | None = None
