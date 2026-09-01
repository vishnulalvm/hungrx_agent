"""Fixed nutrition/macro/micronutrient vocabulary.

Every field here is named explicitly and the models forbid extra keys
(`extra="forbid"`) — this is deliberate and load-bearing: the AI
extraction pipeline (a future task) must never be able to invent a new
nutrient key that silently flows into the database. If a new nutrient
needs tracking, it gets added here explicitly, reviewed like any other
schema change, not auto-created by whatever the model happened to scrape.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

NonNegativeDecimal = Decimal


class Macros(BaseModel):
    """Core macronutrients, per the UI's fixed macro breakdown."""

    model_config = ConfigDict(extra="forbid")

    calories: NonNegativeDecimal | None = Field(default=None, ge=0)
    protein_g: NonNegativeDecimal | None = Field(default=None, ge=0)
    carbohydrates_g: NonNegativeDecimal | None = Field(default=None, ge=0)
    fat_g: NonNegativeDecimal | None = Field(default=None, ge=0)
    saturated_fat_g: NonNegativeDecimal | None = Field(default=None, ge=0)
    trans_fat_g: NonNegativeDecimal | None = Field(default=None, ge=0)
    fiber_g: NonNegativeDecimal | None = Field(default=None, ge=0)
    sugar_g: NonNegativeDecimal | None = Field(default=None, ge=0)
    sodium_mg: NonNegativeDecimal | None = Field(default=None, ge=0)
    cholesterol_mg: NonNegativeDecimal | None = Field(default=None, ge=0)


class Micronutrients(BaseModel):
    """Fixed micronutrient set — same "no arbitrary keys" rule as Macros."""

    model_config = ConfigDict(extra="forbid")

    vitamin_a_mcg: NonNegativeDecimal | None = Field(default=None, ge=0)
    vitamin_c_mg: NonNegativeDecimal | None = Field(default=None, ge=0)
    vitamin_d_mcg: NonNegativeDecimal | None = Field(default=None, ge=0)
    calcium_mg: NonNegativeDecimal | None = Field(default=None, ge=0)
    iron_mg: NonNegativeDecimal | None = Field(default=None, ge=0)
    potassium_mg: NonNegativeDecimal | None = Field(default=None, ge=0)


class Nutrition(BaseModel):
    """Full nutrition profile attached to a Dish. `serving_size` describes
    what the macro/micro values below are measured against (e.g. "1 bowl
    (350g)") — required whenever any nutrient value is present, since a
    nutrient number with no serving context is meaningless."""

    model_config = ConfigDict(extra="forbid")

    serving_size: str | None = None
    macros: Macros = Field(default_factory=Macros)
    micronutrients: Micronutrients = Field(default_factory=Micronutrients)
