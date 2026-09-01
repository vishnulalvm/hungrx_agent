import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.schemas.menu import Menu


class RestaurantLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    label: str | None = None  # e.g. "Downtown", "Airport Terminal 2"

    address_line1: str = Field(min_length=1, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    city: str = Field(min_length=1, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str = Field(min_length=2, max_length=2)  # ISO 3166-1 alpha-2

    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    phone: str | None = None

    @field_validator("country")
    @classmethod
    def uppercase_country(cls, value: str) -> str:
        return value.upper()

    @field_validator("address_line1", "city")
    @classmethod
    def strip_and_require_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("This field cannot be blank")
        return stripped


class Restaurant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)

    logo_url: str | None = None
    cover_image_url: str | None = None
    gallery_image_urls: list[str] = Field(default_factory=list)

    website_url: str | None = None
    cuisine_types: list[str] = Field(default_factory=list)

    locations: list[RestaurantLocation] = Field(default_factory=list)
    menus: list[Menu] = Field(default_factory=list)

    is_active: bool = True

    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def strip_and_require_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Restaurant name cannot be blank")
        return stripped
