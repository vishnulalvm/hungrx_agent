"""Strict schema for AI-generated structured output — this is the ONLY
shape a model is ever allowed to return from the Multimodal Translation
node, not `core.schemas.restaurant.Restaurant`/`Menu`/`Dish` directly.

Two deliberate differences from the "real" domain schemas
(`core/schemas/restaurant.py`, `menu.py`):

  - No `id: uuid.UUID = Field(default_factory=uuid.uuid4)` fields. An AI
    response is not authoritative — it must never be able to assign an
    identity that later code might mistake for a real, persisted primary
    key. Identity assignment happens in Python (the translation node),
    never inside model output.
  - Every extracted node (dish, category, the restaurant profile as a
    whole) carries a `confidence: float` and `source_snapshot_ids: list`
    field, so provenance/confidence metadata comes from the model run
    itself, not bolted on afterward by guesswork.

Every schema here keeps `extra="forbid"` (same rule as the rest of
`core/schemas/`) — a model returning a key we didn't ask for is a
contract violation, not free-form flexibility we quietly accept. Every
optional field is genuinely optional (`| None`), not a place for the
model to invent filler; nothing here allows arbitrary/free-form nested
objects (no `dict[str, Any]`, no untyped extension points) — the model
can only ever populate the fixed fields defined below.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from core.schemas.menu import Allergen
from core.schemas.nutrition import Macros, Micronutrients


class ExtractedNutrition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serving_size: str | None = None
    macros: Macros = Field(default_factory=Macros)
    micronutrients: Micronutrients = Field(default_factory=Micronutrients)


class ExtractedDish(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    image_url: str | None = None

    nutrition: ExtractedNutrition = Field(default_factory=ExtractedNutrition)
    allergens: list[Allergen] = Field(default_factory=list)
    ingredient_names: list[str] = Field(default_factory=list)

    quantity: str | None = None
    price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    # Model-reported confidence in [0, 1] for this specific dish's
    # extracted fields. None when the model has no basis to estimate one
    # (never defaulted to a number that looks like a real signal).
    confidence: float | None = Field(default=None, ge=0, le=1)

    # Which captured source materials (SourceSnapshot.id, as strings)
    # this dish's data was read from — required, not optional: every
    # extracted fact must be traceable to something we actually crawled.
    source_snapshot_ids: list[str] = Field(default_factory=list)


class ExtractedMenuCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    dishes: list[ExtractedDish] = Field(default_factory=list)
    # Flat by design (no `children: list[ExtractedMenuCategory]`) — the
    # model is only asked to bucket dishes into named groups, not to
    # invent an arbitrarily deep tree. Sub-categorization deeper than one
    # level is a human-review-time concern, not something worth trusting
    # the model's judgment on here.


class ExtractedMenu(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Menu", min_length=1, max_length=255)
    categories: list[ExtractedMenuCategory] = Field(default_factory=list)


class ExtractedRestaurantProfile(BaseModel):
    """Restaurant-level fields the model may fill in from the crawled
    source material — deliberately excludes anything identity- or
    location-sensitive (name, address) that Source Authority/the caller
    already knows with certainty; the model only adds descriptive
    metadata it can actually read off the page."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=2000)
    cuisine_types: list[str] = Field(default_factory=list)
    logo_url: str | None = None
    cover_image_url: str | None = None

    confidence: float | None = Field(default=None, ge=0, le=1)
    source_snapshot_ids: list[str] = Field(default_factory=list)


class ExtractionOutput(BaseModel):
    """The complete, strict shape an AIProvider call for Multimodal
    Translation must return. This is passed directly as the structured-
    output schema to the model — the model physically cannot return
    anything outside this shape (see infrastructure/ai/openai_provider.py),
    which is what makes "do not allow free-form output" an enforced
    constraint rather than a prompt instruction."""

    model_config = ConfigDict(extra="forbid")

    restaurant_profile: ExtractedRestaurantProfile = Field(default_factory=ExtractedRestaurantProfile)
    menus: list[ExtractedMenu] = Field(default_factory=list)
