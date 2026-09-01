"""Extensive unit tests for the deterministic validation engine
(core.validation) — pure, no DB/network/AI, same input always produces
the same output. Covers every rule category from the Agent 4 spec:
Pydantic/schema validation, nutrition constraints + Atwater checks,
allergen taxonomy checks, price validation, required-field validation,
duplicate detection, impossible-value detection, and safe corrections
(with an explicit guarantee that AI-generated numeric/semantic data is
never silently changed).
"""

from decimal import Decimal

from core.schemas.menu import Allergen, Dish, Ingredient, Menu, MenuCategory
from core.schemas.nutrition import Macros, Nutrition
from core.schemas.restaurant import Restaurant, RestaurantLocation
from core.validation import validate
from core.validation.result import ValidationSeverity


def _location() -> RestaurantLocation:
    return RestaurantLocation(address_line1="1 Main St", city="Springfield", country="US")


def _dish(**overrides) -> Dish:
    import uuid

    payload = {
        "category_id": uuid.uuid4(),
        "name": "Margherita Pizza",
        "price": Decimal("12.99"),
        "currency": "USD",
    }
    payload.update(overrides)
    return Dish(**payload)


def _restaurant(*, menus: list[Menu] | None = None, locations: list | None = None) -> Restaurant:
    return Restaurant(
        name="Joe's Pizza",
        locations=[_location()] if locations is None else locations,
        menus=menus or [],
    )


def _restaurant_with_dishes(*dishes: Dish) -> Restaurant:
    category = MenuCategory(name="Pizzas", dishes=list(dishes))
    menu = Menu(name="Main Menu", categories=[category])
    return _restaurant(menus=[menu])


class TestSchemaValidation:
    def test_valid_restaurant_dict_is_valid(self) -> None:
        restaurant = _restaurant_with_dishes(_dish())
        outcome = validate(restaurant.model_dump(mode="json"))
        assert outcome.is_valid

    def test_malformed_payload_is_invalid_with_schema_violation_code(self) -> None:
        outcome = validate({"name": "", "menus": []})
        assert not outcome.is_valid
        assert any(issue.code == "schema_violation" for issue in outcome.errors)

    def test_missing_required_field_reports_schema_violation(self) -> None:
        outcome = validate({"menus": []})  # no name at all
        assert not outcome.is_valid
        assert any(issue.code == "schema_violation" for issue in outcome.errors)

    def test_extra_unexpected_field_is_rejected(self) -> None:
        payload = _restaurant().model_dump(mode="json")
        payload["made_up_field"] = "not allowed"
        outcome = validate(payload)
        assert not outcome.is_valid

    def test_already_constructed_restaurant_is_accepted_directly(self) -> None:
        restaurant = _restaurant_with_dishes(_dish())
        outcome = validate(restaurant)
        assert outcome.is_valid

    def test_invalid_payload_produces_no_corrected_restaurant(self) -> None:
        outcome = validate({"name": ""})
        assert outcome.corrected_restaurant is None


class TestNutritionConstraints:
    def test_atwater_consistent_calories_produce_no_warning(self) -> None:
        # 20g protein + 30g carbs + 10g fat = 80+120+90 = 290 kcal
        nutrition = Nutrition(
            serving_size="1 slice",
            macros=Macros(calories=Decimal(290), protein_g=Decimal(20), carbohydrates_g=Decimal(30), fat_g=Decimal(10)),
        )
        dish = _dish(nutrition=nutrition)
        outcome = validate(_restaurant_with_dishes(dish))
        assert not any(issue.code == "atwater_mismatch" for issue in outcome.warnings)

    def test_atwater_wildly_inconsistent_calories_produce_a_warning(self) -> None:
        # macros imply ~290 kcal but calories claims 2000
        nutrition = Nutrition(
            serving_size="1 slice",
            macros=Macros(calories=Decimal(2000), protein_g=Decimal(20), carbohydrates_g=Decimal(30), fat_g=Decimal(10)),
        )
        dish = _dish(nutrition=nutrition)
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(issue.code == "atwater_mismatch" for issue in outcome.warnings)

    def test_atwater_mismatch_is_a_warning_not_an_error(self) -> None:
        nutrition = Nutrition(
            serving_size="1 slice",
            macros=Macros(calories=Decimal(2000), protein_g=Decimal(20), carbohydrates_g=Decimal(30), fat_g=Decimal(10)),
        )
        dish = _dish(nutrition=nutrition)
        outcome = validate(_restaurant_with_dishes(dish))
        assert outcome.is_valid  # warnings never affect validity

    def test_atwater_check_skipped_when_macros_incomplete(self) -> None:
        nutrition = Nutrition(serving_size="1 slice", macros=Macros(calories=Decimal(9999)))
        dish = _dish(nutrition=nutrition)
        outcome = validate(_restaurant_with_dishes(dish))
        # No atwater_mismatch — can't cross-check without all three macros
        assert not any(issue.code == "atwater_mismatch" for issue in outcome.warnings + outcome.errors)

    def test_missing_serving_size_with_nutrition_values_warns(self) -> None:
        nutrition = Nutrition(macros=Macros(calories=Decimal(300)))
        dish = _dish(nutrition=nutrition)
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(issue.code == "missing_serving_size" for issue in outcome.warnings)

    def test_no_nutrition_values_does_not_warn_about_serving_size(self) -> None:
        dish = _dish()
        outcome = validate(_restaurant_with_dishes(dish))
        assert not any(issue.code == "missing_serving_size" for issue in outcome.warnings)

    def test_implausible_calories_is_an_error(self) -> None:
        nutrition = Nutrition(serving_size="1 tray", macros=Macros(calories=Decimal(9000)))
        dish = _dish(nutrition=nutrition)
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(issue.code == "implausible_calories" for issue in outcome.errors)
        assert not outcome.is_valid

    def test_implausible_sodium_is_an_error(self) -> None:
        nutrition = Nutrition(serving_size="1 tray", macros=Macros(sodium_mg=Decimal(20000)))
        dish = _dish(nutrition=nutrition)
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(issue.code == "implausible_sodium" for issue in outcome.errors)

    def test_plausible_calories_do_not_error(self) -> None:
        nutrition = Nutrition(serving_size="1 slice", macros=Macros(calories=Decimal(300)))
        dish = _dish(nutrition=nutrition)
        outcome = validate(_restaurant_with_dishes(dish))
        assert not any(issue.code == "implausible_calories" for issue in outcome.errors)


class TestImpossibleValueDetection:
    def test_saturated_fat_exceeding_total_fat_is_an_error(self) -> None:
        nutrition = Nutrition(macros=Macros(fat_g=Decimal(5), saturated_fat_g=Decimal(10)))
        dish = _dish(nutrition=nutrition)
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(issue.code == "saturated_fat_exceeds_total_fat" for issue in outcome.errors)
        assert not outcome.is_valid

    def test_trans_fat_exceeding_total_fat_is_an_error(self) -> None:
        nutrition = Nutrition(macros=Macros(fat_g=Decimal(5), trans_fat_g=Decimal(6)))
        dish = _dish(nutrition=nutrition)
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(issue.code == "trans_fat_exceeds_total_fat" for issue in outcome.errors)

    def test_sugar_exceeding_total_carbohydrates_is_an_error(self) -> None:
        nutrition = Nutrition(macros=Macros(carbohydrates_g=Decimal(10), sugar_g=Decimal(15)))
        dish = _dish(nutrition=nutrition)
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(issue.code == "sugar_exceeds_total_carbohydrates" for issue in outcome.errors)

    def test_fiber_exceeding_total_carbohydrates_is_an_error(self) -> None:
        nutrition = Nutrition(macros=Macros(carbohydrates_g=Decimal(10), fiber_g=Decimal(20)))
        dish = _dish(nutrition=nutrition)
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(issue.code == "fiber_exceeds_total_carbohydrates" for issue in outcome.errors)

    def test_subset_values_within_bounds_do_not_error(self) -> None:
        nutrition = Nutrition(
            macros=Macros(
                fat_g=Decimal(10),
                saturated_fat_g=Decimal(3),
                trans_fat_g=Decimal(0),
                carbohydrates_g=Decimal(30),
                sugar_g=Decimal(10),
                fiber_g=Decimal(5),
            )
        )
        dish = _dish(nutrition=nutrition)
        outcome = validate(_restaurant_with_dishes(dish))
        impossible_codes = {
            "saturated_fat_exceeds_total_fat",
            "trans_fat_exceeds_total_fat",
            "sugar_exceeds_total_carbohydrates",
            "fiber_exceeds_total_carbohydrates",
        }
        assert not any(issue.code in impossible_codes for issue in outcome.errors)


class TestAllergenTaxonomyChecks:
    def test_ingredient_implying_undeclared_allergen_warns(self) -> None:
        dish = _dish(ingredients=[Ingredient(name="cheddar cheese")], allergens=[])
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(issue.code == "possible_undeclared_allergen" for issue in outcome.warnings)

    def test_correctly_declared_allergen_does_not_warn(self) -> None:
        dish = _dish(ingredients=[Ingredient(name="cheddar cheese")], allergens=[Allergen.MILK])
        outcome = validate(_restaurant_with_dishes(dish))
        assert not any(issue.code == "possible_undeclared_allergen" for issue in outcome.warnings)

    def test_no_allergen_implying_ingredients_produces_no_warning(self) -> None:
        dish = _dish(ingredients=[Ingredient(name="basil")], allergens=[])
        outcome = validate(_restaurant_with_dishes(dish))
        assert not any(issue.code == "possible_undeclared_allergen" for issue in outcome.warnings)

    def test_peanut_ingredient_implies_peanuts_allergen(self) -> None:
        dish = _dish(ingredients=[Ingredient(name="crushed peanuts")], allergens=[])
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(
            issue.code == "possible_undeclared_allergen" and "peanuts" in issue.message
            for issue in outcome.warnings
        )

    def test_taxonomy_check_is_a_warning_not_an_error(self) -> None:
        dish = _dish(ingredients=[Ingredient(name="shrimp")], allergens=[])
        outcome = validate(_restaurant_with_dishes(dish))
        assert outcome.is_valid


class TestPriceValidation:
    def test_normal_price_is_valid(self) -> None:
        dish = _dish(price=Decimal("15.00"), currency="USD")
        outcome = validate(_restaurant_with_dishes(dish))
        assert outcome.is_valid

    def test_implausibly_high_price_is_an_error(self) -> None:
        dish = _dish(price=Decimal("999999"), currency="USD")
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(issue.code == "implausible_price" for issue in outcome.errors)

    def test_zero_price_is_a_warning(self) -> None:
        dish = _dish(price=Decimal("0"), currency="USD")
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(issue.code == "zero_price" for issue in outcome.warnings)
        assert outcome.is_valid

    def test_negative_price_cannot_even_construct_a_dish(self) -> None:
        import pytest

        with pytest.raises(Exception):
            _dish(price=Decimal("-5"))

    def test_malformed_currency_code_is_an_error(self) -> None:
        dish = _dish(currency="US$")
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(issue.code == "malformed_currency_code" for issue in outcome.errors)

    def test_lowercase_currency_code_is_corrected_rather_than_left_as_a_warning(self) -> None:
        # Safe corrections run before rule checks, so a lowercase currency
        # code is fixed (see TestSafeCorrections) before check_price ever
        # sees it — no stale "lowercase_currency_code" warning survives
        # once the correction itself has resolved it.
        dish = _dish(currency="usd")
        outcome = validate(_restaurant_with_dishes(dish))
        assert not any(issue.code == "lowercase_currency_code" for issue in outcome.warnings)
        assert any(field.field_path.endswith(".currency") for field in outcome.corrected_fields)

    def test_check_price_rule_itself_flags_lowercase_currency(self) -> None:
        # Unit-tests the price_rules module directly (bypassing
        # safe_corrections, which the full validate() pipeline always
        # runs first) to confirm the rule itself is correct in isolation.
        from core.validation.price_rules import check_price

        dish = _dish(currency="usd")
        issues = check_price(dish, field_prefix="dish")
        assert any(issue.code == "lowercase_currency_code" for issue in issues)


class TestRequiredFieldValidation:
    def test_restaurant_with_no_locations_warns(self) -> None:
        restaurant = _restaurant_with_dishes(_dish())
        restaurant = restaurant.model_copy(update={"locations": []})
        outcome = validate(restaurant)
        assert any(issue.code == "missing_locations" for issue in outcome.warnings)

    def test_restaurant_with_no_menus_warns(self) -> None:
        restaurant = _restaurant(menus=[])
        outcome = validate(restaurant)
        assert any(issue.code == "missing_menus" for issue in outcome.warnings)

    def test_dish_with_no_price_warns(self) -> None:
        dish = _dish(price=None)
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(issue.code == "missing_price" for issue in outcome.warnings)

    def test_dish_with_no_nutrition_warns(self) -> None:
        dish = _dish()
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(issue.code == "missing_nutrition" for issue in outcome.warnings)

    def test_fully_populated_dish_has_no_required_field_warnings(self) -> None:
        nutrition = Nutrition(serving_size="1 slice", macros=Macros(calories=Decimal(300)))
        dish = _dish(nutrition=nutrition, price=Decimal("10.00"))
        outcome = validate(_restaurant_with_dishes(dish))
        assert not any(
            issue.code in {"missing_price", "missing_nutrition"} for issue in outcome.warnings
        )


class TestDuplicateDetection:
    def test_exact_duplicate_dish_names_are_flagged(self) -> None:
        dish_a = _dish(name="Margherita Pizza")
        dish_b = _dish(name="Margherita Pizza")
        outcome = validate(_restaurant_with_dishes(dish_a, dish_b))
        assert any(issue.code == "duplicate_dish_name" for issue in outcome.warnings)

    def test_case_and_whitespace_insensitive_duplicate_detection(self) -> None:
        dish_a = _dish(name="Margherita Pizza")
        dish_b = _dish(name="  MARGHERITA   PIZZA  ")
        outcome = validate(_restaurant_with_dishes(dish_a, dish_b))
        assert any(issue.code == "duplicate_dish_name" for issue in outcome.warnings)

    def test_different_dish_names_are_not_flagged(self) -> None:
        dish_a = _dish(name="Margherita Pizza")
        dish_b = _dish(name="Pepperoni Pizza")
        outcome = validate(_restaurant_with_dishes(dish_a, dish_b))
        assert not any(issue.code == "duplicate_dish_name" for issue in outcome.warnings)

    def test_similar_but_distinct_names_are_not_flagged(self) -> None:
        # Not a fuzzy matcher on purpose — "Chicken Sandwich" vs "Chicken
        # Sandwich Combo" are different items, not duplicates.
        dish_a = _dish(name="Chicken Sandwich")
        dish_b = _dish(name="Chicken Sandwich Combo")
        outcome = validate(_restaurant_with_dishes(dish_a, dish_b))
        assert not any(issue.code == "duplicate_dish_name" for issue in outcome.warnings)

    def test_duplicate_across_different_categories_is_still_flagged(self) -> None:
        category_a = MenuCategory(name="Pizzas", dishes=[_dish(name="House Special")])
        category_b = MenuCategory(name="Specials", dishes=[_dish(name="House Special")])
        menu = Menu(categories=[category_a, category_b])
        restaurant = _restaurant(menus=[menu])
        outcome = validate(restaurant)
        assert any(issue.code == "duplicate_dish_name" for issue in outcome.warnings)

    def test_duplicate_detection_is_a_warning_not_an_error(self) -> None:
        dish_a = _dish(name="Margherita Pizza")
        dish_b = _dish(name="Margherita Pizza")
        outcome = validate(_restaurant_with_dishes(dish_a, dish_b))
        assert outcome.is_valid

    def test_three_identical_names_flags_all_three(self) -> None:
        dishes = [_dish(name="Special") for _ in range(3)]
        outcome = validate(_restaurant_with_dishes(*dishes))
        duplicate_issues = [issue for issue in outcome.warnings if issue.code == "duplicate_dish_name"]
        assert len(duplicate_issues) == 3


class TestSafeCorrections:
    def test_lowercase_currency_is_corrected(self) -> None:
        dish = _dish(currency="usd")
        outcome = validate(_restaurant_with_dishes(dish))
        assert any(field.field_path.endswith(".currency") for field in outcome.corrected_fields)
        corrected_dish = outcome.corrected_restaurant.menus[0].categories[0].dishes[0]
        assert corrected_dish.currency == "USD"

    def test_whitespace_in_description_is_collapsed(self) -> None:
        dish = _dish(description="Tomato,    basil   and   mozzarella")
        outcome = validate(_restaurant_with_dishes(dish))
        corrected_dish = outcome.corrected_restaurant.menus[0].categories[0].dishes[0]
        assert corrected_dish.description == "Tomato, basil and mozzarella"
        assert any(field.field_path.endswith(".description") for field in outcome.corrected_fields)

    def test_duplicate_ingredients_are_deduplicated(self) -> None:
        dish = _dish(ingredients=[Ingredient(name="Basil"), Ingredient(name="basil")])
        outcome = validate(_restaurant_with_dishes(dish))
        corrected_dish = outcome.corrected_restaurant.menus[0].categories[0].dishes[0]
        assert len(corrected_dish.ingredients) == 1
        assert any(field.field_path.endswith(".ingredients") for field in outcome.corrected_fields)

    def test_no_corrections_needed_returns_empty_corrected_fields(self) -> None:
        nutrition = Nutrition(serving_size="1 slice", macros=Macros(calories=Decimal(300)))
        dish = _dish(nutrition=nutrition, currency="USD", description="Clean text")
        outcome = validate(_restaurant_with_dishes(dish))
        assert outcome.corrected_fields == []

    def test_correction_never_touches_calories_or_price(self) -> None:
        # Even when Atwater math is wildly off, the calorie/price values
        # themselves must appear unchanged in corrected_restaurant — only
        # a ValidationIssue is produced, never a rewrite.
        nutrition = Nutrition(
            serving_size="1 slice",
            macros=Macros(calories=Decimal(9999), protein_g=Decimal(1), carbohydrates_g=Decimal(1), fat_g=Decimal(1)),
        )
        dish = _dish(nutrition=nutrition, price=Decimal("999999"))
        outcome = validate(_restaurant_with_dishes(dish))
        corrected_dish = outcome.corrected_restaurant.menus[0].categories[0].dishes[0]
        assert corrected_dish.nutrition.macros.calories == Decimal(9999)
        assert corrected_dish.price == Decimal("999999")

    def test_correction_never_touches_allergens(self) -> None:
        dish = _dish(ingredients=[Ingredient(name="peanuts")], allergens=[])
        outcome = validate(_restaurant_with_dishes(dish))
        corrected_dish = outcome.corrected_restaurant.menus[0].categories[0].dishes[0]
        assert corrected_dish.allergens == []  # not auto-added despite the warning

    def test_correction_never_touches_dish_name(self) -> None:
        dish = _dish(name="  Margherita   Pizza  ".strip())
        # Pydantic's own validator already trims/collapses on construction
        # for name; verify the validator's corrections layer doesn't
        # additionally rewrite it beyond what the schema itself did.
        outcome = validate(_restaurant_with_dishes(dish))
        corrected_dish = outcome.corrected_restaurant.menus[0].categories[0].dishes[0]
        assert corrected_dish.name == dish.name

    def test_recorded_correction_includes_old_and_new_value(self) -> None:
        dish = _dish(currency="usd")
        outcome = validate(_restaurant_with_dishes(dish))
        currency_correction = next(f for f in outcome.corrected_fields if f.field_path.endswith(".currency"))
        assert currency_correction.old_value == "usd"
        assert currency_correction.new_value == "USD"
        assert currency_correction.reason


class TestDeterminism:
    def test_same_input_produces_identical_outcome_across_runs(self) -> None:
        restaurant = _restaurant_with_dishes(_dish(currency="usd"), _dish(name="Margherita Pizza"))
        first = validate(restaurant)
        second = validate(restaurant)
        assert [e.code for e in first.errors] == [e.code for e in second.errors]
        assert [w.code for w in first.warnings] == [w.code for w in second.warnings]
        assert len(first.corrected_fields) == len(second.corrected_fields)

    def test_validate_never_mutates_the_input_restaurant(self) -> None:
        dish = _dish(currency="usd")
        restaurant = _restaurant_with_dishes(dish)
        original_currency = restaurant.menus[0].categories[0].dishes[0].currency
        validate(restaurant)
        assert restaurant.menus[0].categories[0].dishes[0].currency == original_currency


class TestValidOverallOutcome:
    def test_clean_restaurant_is_fully_valid_with_no_errors(self) -> None:
        nutrition = Nutrition(
            serving_size="1 slice",
            macros=Macros(calories=Decimal(290), protein_g=Decimal(20), carbohydrates_g=Decimal(30), fat_g=Decimal(10)),
        )
        dish = _dish(nutrition=nutrition, price=Decimal("12.99"), currency="USD")
        outcome = validate(_restaurant_with_dishes(dish))
        assert outcome.is_valid
        assert outcome.errors == []

    def test_multiple_errors_across_different_rules_are_all_reported(self) -> None:
        nutrition = Nutrition(macros=Macros(fat_g=Decimal(5), saturated_fat_g=Decimal(10)))
        dish = _dish(nutrition=nutrition, price=Decimal("999999"), currency="US$")
        outcome = validate(_restaurant_with_dishes(dish))
        codes = {issue.code for issue in outcome.errors}
        assert "saturated_fat_exceeds_total_fat" in codes
        assert "implausible_price" in codes
        assert "malformed_currency_code" in codes
        assert not outcome.is_valid
