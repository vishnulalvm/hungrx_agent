"""Menu / category / dish schemas.

MenuCategory nests to arbitrary depth (category -> subcategory -> ...) to
match the UI's tree structure; Dish carries the fixed field set the UI
renders per item, plus a review/confirmation state so the admin review
queue (a future task) has something typed to work against instead of a
bare status string.
"""

import enum
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.schemas.nutrition import Nutrition


class Allergen(str, enum.Enum):
    MILK = "milk"
    EGGS = "eggs"
    FISH = "fish"
    SHELLFISH = "shellfish"
    TREE_NUTS = "tree_nuts"
    PEANUTS = "peanuts"
    WHEAT = "wheat"
    SOY = "soy"
    SESAME = "sesame"
    GLUTEN = "gluten"


class Ingredient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    is_optional: bool = False

    @field_validator("name")
    @classmethod
    def strip_and_require_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Ingredient name cannot be blank")
        return stripped


class ReviewState(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class Dish(BaseModel):
    """Fixed field set matching the UI's dish detail view. `category_id`
    links back to the owning MenuCategory rather than embedding the dish
    inside the category's own `dishes` list being the only place it can
    live — useful once dishes need to be queried/edited independently of
    walking the whole category tree."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    category_id: uuid.UUID

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    image_url: str | None = None

    nutrition: Nutrition = Field(default_factory=Nutrition)
    allergens: list[Allergen] = Field(default_factory=list)
    ingredients: list[Ingredient] = Field(default_factory=list)

    quantity: str | None = None  # e.g. "12 oz", "1 piece" — free-text serving descriptor
    price: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)

    review_state: ReviewState = ReviewState.PENDING

    @field_validator("name")
    @classmethod
    def strip_and_require_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Dish name cannot be blank")
        return stripped

    @field_validator("allergens")
    @classmethod
    def unique_allergens(cls, value: list[Allergen]) -> list[Allergen]:
        seen = list(dict.fromkeys(value))
        return seen


class MenuCategory(BaseModel):
    """Recursive category tree — `children` holds subcategories at
    arbitrary depth, matching the UI's category -> subcategory structure.
    A category with a blank/whitespace-only name is rejected outright
    rather than silently accepted and rendered as an empty tree node."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str = Field(min_length=1, max_length=255)
    display_order: int = 0

    children: list["MenuCategory"] = Field(default_factory=list)
    dishes: list[Dish] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def strip_and_require_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Category name cannot be blank")
        return stripped


MenuCategory.model_rebuild()


class Menu(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str = Field(default="Menu", min_length=1, max_length=255)
    categories: list[MenuCategory] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def strip_and_require_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Menu name cannot be blank")
        return stripped
