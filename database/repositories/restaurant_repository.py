"""Writes the production restaurant/menu tables. This repository is
deliberately the ONLY place that inserts into restaurants/
restaurant_locations/menus/menu_categories/dishes — it is imported
exclusively by workflows/collector_workflow/nodes/publish.py, which only
ever calls it after confirming human_approval_status ==
ProposedChangeStatus.APPROVED. Nothing else in the codebase should import
this module; that's what makes "unapproved data never reaches production
tables" a structural guarantee rather than a runtime check scattered
across callers.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.restaurant import Restaurant as RestaurantSchema
from database.models.restaurant import Dish, Menu, MenuCategory, Restaurant, RestaurantLocation


class RestaurantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def persist_tree(self, restaurant: RestaurantSchema) -> Restaurant:
        """Inserts a complete Restaurant → locations/menus → categories
        (recursive) → dishes tree from a validated core.schemas.restaurant.
        Restaurant. Always an insert of a brand-new row set (published
        collector runs create a new restaurant record each time in this
        version — updating/merging into an existing restaurant on
        republish is out of scope here) — the caller owns the
        transaction/commit, this only flushes so IDs are available."""
        record = Restaurant(
            id=restaurant.id,
            name=restaurant.name,
            description=restaurant.description,
            logo_url=restaurant.logo_url,
            cover_image_url=restaurant.cover_image_url,
            gallery_image_urls=list(restaurant.gallery_image_urls),
            website_url=restaurant.website_url,
            cuisine_types=list(restaurant.cuisine_types),
            is_active=restaurant.is_active,
        )
        self._session.add(record)

        for location in restaurant.locations:
            self._session.add(
                RestaurantLocation(
                    id=location.id,
                    restaurant_id=restaurant.id,
                    label=location.label,
                    address_line1=location.address_line1,
                    address_line2=location.address_line2,
                    city=location.city,
                    state=location.state,
                    postal_code=location.postal_code,
                    country=location.country,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    phone=location.phone,
                )
            )

        for menu in restaurant.menus:
            menu_record = Menu(id=menu.id, restaurant_id=restaurant.id, name=menu.name)
            self._session.add(menu_record)
            for category in menu.categories:
                self._persist_category(category, menu_id=menu.id, parent_id=None)

        await self._session.flush()
        return record

    def _persist_category(self, category, *, menu_id: uuid.UUID, parent_id: uuid.UUID | None) -> None:
        category_record = MenuCategory(
            id=category.id,
            menu_id=menu_id,
            parent_id=parent_id,
            name=category.name,
            display_order=category.display_order,
        )
        self._session.add(category_record)

        for dish in category.dishes:
            self._session.add(
                Dish(
                    id=dish.id,
                    category_id=category.id,
                    name=dish.name,
                    description=dish.description,
                    image_url=dish.image_url,
                    nutrition=dish.nutrition.model_dump(mode="json"),
                    allergens=[allergen.value for allergen in dish.allergens],
                    ingredients=[ingredient.model_dump(mode="json") for ingredient in dish.ingredients],
                    quantity=dish.quantity,
                    price=dish.price,
                    currency=dish.currency,
                    review_state=dish.review_state,
                )
            )

        for child in category.children:
            self._persist_category(child, menu_id=menu_id, parent_id=category.id)
