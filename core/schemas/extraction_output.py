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

A third difference, narrower but load-bearing: every numeric field here
is `float`, never `Decimal` — unlike `core.schemas.menu.Dish.price` and
`core.schemas.nutrition.Macros`/`Micronutrients`, which use `Decimal` for
real monetary/nutrition precision. OpenAI's strict `json_schema`
structured-output mode (infrastructure/ai/openai_provider.py) cannot
satisfy the schema Pydantic generates for `Decimal` (a number-or-pattern-
constrained-string union) — a request built from a `Decimal` field is
silently rejected by the model with zero output tokens
(`finish_reason="length"`, no error), confirmed against a real account.
`ExtractedMacros`/`ExtractedMicronutrients` mirror
`core.schemas.nutrition.Macros`/`Micronutrients` field-for-field but with
`float`; the translation nodes convert float → Decimal when mapping into
the real `Nutrition`/`Dish` domain objects, so nothing downstream of that
mapping ever sees a float where money/nutrition precision is expected.

Every schema here keeps `extra="forbid"` (same rule as the rest of
`core/schemas/`) — a model returning a key we didn't ask for is a
contract violation, not free-form flexibility we quietly accept. Every
optional field is genuinely optional (`| None`), not a place for the
model to invent filler; nothing here allows arbitrary/free-form nested
objects (no `dict[str, Any]`, no untyped extension points) — the model
can only ever populate the fixed fields defined below.
"""

from pydantic import BaseModel, ConfigDict, Field

from core.schemas.menu import Allergen


class ExtractedMacros(BaseModel):
    """Float mirror of `core.schemas.nutrition.Macros` — see module
    docstring for why `Decimal` cannot be used in AI-facing schemas."""

    model_config = ConfigDict(extra="forbid")

    calories: float | None = Field(default=None, ge=0)
    protein_g: float | None = Field(default=None, ge=0)
    carbohydrates_g: float | None = Field(default=None, ge=0)
    fat_g: float | None = Field(default=None, ge=0)
    saturated_fat_g: float | None = Field(default=None, ge=0)
    trans_fat_g: float | None = Field(default=None, ge=0)
    fiber_g: float | None = Field(default=None, ge=0)
    sugar_g: float | None = Field(default=None, ge=0)
    sodium_mg: float | None = Field(default=None, ge=0)
    cholesterol_mg: float | None = Field(default=None, ge=0)


class ExtractedMicronutrients(BaseModel):
    """Float mirror of `core.schemas.nutrition.Micronutrients` — see
    module docstring for why `Decimal` cannot be used in AI-facing
    schemas."""

    model_config = ConfigDict(extra="forbid")

    vitamin_a_mcg: float | None = Field(default=None, ge=0)
    vitamin_c_mg: float | None = Field(default=None, ge=0)
    vitamin_d_mcg: float | None = Field(default=None, ge=0)
    calcium_mg: float | None = Field(default=None, ge=0)
    iron_mg: float | None = Field(default=None, ge=0)
    potassium_mg: float | None = Field(default=None, ge=0)


class ExtractedNutrition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serving_size: str | None = None
    macros: ExtractedMacros = Field(default_factory=ExtractedMacros)
    micronutrients: ExtractedMicronutrients = Field(default_factory=ExtractedMicronutrients)


class ExtractedDish(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    image_url: str | None = None

    nutrition: ExtractedNutrition = Field(default_factory=ExtractedNutrition)
    allergens: list[Allergen] = Field(default_factory=list)
    ingredient_names: list[str] = Field(default_factory=list)

    quantity: str | None = None
    price: float | None = Field(default=None, ge=0)
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
    """The complete, strict shape Multimodal Translation ultimately
    assembles for the rest of the pipeline. Never sent to the model as a
    single `response_model` in one call — its full nested shape (dish →
    nutrition → macros/micronutrients, repeated across every category of
    every menu) is too large for OpenAI's strict structured-output mode
    to reliably fill in (see infrastructure/ai/chunked_extraction.py for
    why, and the two-phase call pattern used instead). Only the *pieces*
    below (`ExtractedDiscovery`, `ExtractedCategoryDishes`) are ever
    actually passed as `response_model`; this type is assembled from
    their results in Python."""

    model_config = ConfigDict(extra="forbid")

    restaurant_profile: ExtractedRestaurantProfile = Field(default_factory=ExtractedRestaurantProfile)
    menus: list[ExtractedMenu] = Field(default_factory=list)


class ExtractedMenuCategoryStub(BaseModel):
    """A menu category's name only, with no dishes yet — phase 1's
    (`ExtractedDiscovery`) job is to discover the menu's structure, not
    populate it; phase 2 (`ExtractedCategoryDishes`, one AI call per
    stub) fills in the dishes for one category at a time. Small and
    flat by design, to keep phase 1's own schema well within strict
    mode's limits."""

    model_config = ConfigDict(extra="forbid")

    menu_name: str = Field(default="Menu", min_length=1, max_length=255)
    category_name: str = Field(min_length=1, max_length=255)


class ExtractedDiscovery(BaseModel):
    """Phase 1's `response_model`: the restaurant profile plus the flat
    list of (menu, category) names found in the source material — no
    dish-level detail. Kept deliberately small so this call can't hit
    the same strict-mode failure `ExtractionOutput` does."""

    model_config = ConfigDict(extra="forbid")

    restaurant_profile: ExtractedRestaurantProfile = Field(default_factory=ExtractedRestaurantProfile)
    categories: list[ExtractedMenuCategoryStub] = Field(default_factory=list)


class ExtractedCategoryDishes(BaseModel):
    """Phase 2's `response_model`: one AI call per
    `ExtractedMenuCategoryStub` from phase 1, asking only for that
    category's dishes. Scoping each call to a single category (rather
    than the whole menu at once) is what keeps every individual call's
    schema — one `ExtractedDish` at a time, at most a handful per call —
    small enough for OpenAI's strict structured-output mode."""

    model_config = ConfigDict(extra="forbid")

    dishes: list[ExtractedDish] = Field(default_factory=list)
