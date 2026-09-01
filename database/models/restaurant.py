"""Production restaurant/menu tables — the only tables the Publish node
(workflows/collector_workflow/nodes/publish.py) is allowed to write to.
Mirrors core.schemas.restaurant/menu 1:1 in shape; nutrition/allergens/
ingredients are stored as JSONB on `dishes` rather than further
normalized (they're read/written as whole units via core.schemas.nutrition.
Nutrition, never queried by individual nutrient column, so normalizing
them into their own tables would add join complexity with no real
benefit).

Nothing outside the Publish node writes here — this is the structural
half of "do not allow unapproved data into production tables"; the other
half (nothing reaches these tables without an APPROVED ProposedChange)
lives in workflows/collector_workflow/nodes/publish.py and the
human_review node's interrupt/resume flow.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.schemas.menu import ReviewState
from database.models.base import Base


class Restaurant(Base):
    __tablename__ = "restaurants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    logo_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    cover_image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    gallery_image_urls: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    website_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    cuisine_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    locations: Mapped[list["RestaurantLocation"]] = relationship(
        back_populates="restaurant", cascade="all, delete-orphan"
    )
    menus: Mapped[list["Menu"]] = relationship(back_populates="restaurant", cascade="all, delete-orphan")


class RestaurantLocation(Base):
    __tablename__ = "restaurant_locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False)

    latitude: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    restaurant: Mapped[Restaurant] = relationship(back_populates="locations")


class Menu(Base):
    __tablename__ = "menus"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="Menu")

    restaurant: Mapped[Restaurant] = relationship(back_populates="menus")
    categories: Mapped[list["MenuCategory"]] = relationship(
        back_populates="menu", cascade="all, delete-orphan"
    )


class MenuCategory(Base):
    """Self-referential via `parent_id` to mirror
    core.schemas.menu.MenuCategory's recursive `children` — arbitrary
    nesting depth, same as the Pydantic schema."""

    __tablename__ = "menu_categories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    menu_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menus.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_categories.id", ondelete="CASCADE"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    menu: Mapped[Menu] = relationship(back_populates="categories")
    dishes: Mapped[list["Dish"]] = relationship(back_populates="category", cascade="all, delete-orphan")


class Dish(Base):
    __tablename__ = "dishes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_categories.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Whole-unit JSONB — written/read as core.schemas.nutrition.Nutrition
    # and lists of core.schemas.menu.Allergen/Ingredient respectively;
    # never queried by individual key, so normalizing further would add
    # join overhead with no query benefit.
    nutrition: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    allergens: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    ingredients: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)

    quantity: Mapped[str | None] = mapped_column(String(100), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    review_state: Mapped[ReviewState] = mapped_column(
        Enum(ReviewState, name="dish_review_state", native_enum=True),
        nullable=False,
        default=ReviewState.PENDING,
    )

    category: Mapped[MenuCategory] = relationship(back_populates="dishes")
