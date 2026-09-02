from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class HealthStatus(BaseModel):
    status: str
    service: str
    version: str


class PaginationParams(BaseModel):
    page: int = 1
    page_size: int = 20


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int
