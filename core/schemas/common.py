from pydantic import BaseModel


class HealthStatus(BaseModel):
    status: str
    service: str
    version: str


class PaginationParams(BaseModel):
    page: int = 1
    page_size: int = 20


class PaginatedResponse[T](BaseModel):
    items: list[T]
    page: int
    page_size: int
    total: int
