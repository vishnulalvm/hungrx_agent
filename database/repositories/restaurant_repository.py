"""Reads and writes the production restaurant/menu tables.

`persist_tree` is deliberately the ONLY code path that inserts into
restaurants/restaurant_locations/menus/menu_categories/dishes — it is
called exclusively by workflows/collector_workflow/nodes/publish.py,
which only ever calls it after confirming human_approval_status ==
ProposedChangeStatus.APPROVED. No other write method exists on this
class, and nothing else in the codebase should add one; that's what
makes "unapproved data never reaches production tables" a structural
guarantee rather than a runtime check scattered across callers.

`get_full_tree` is read-only and has no such restriction — it's used by
workflows/reviewer_workflow/nodes/json_delta_generation.py to diff a
fresh re-crawl against what's actually live. Reading published data
imposes no risk the write-path guarantee above is protecting against;
only writes are restricted.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.schemas.menu import Dish as DishSchema
from core.schemas.menu import Ingredient, Menu as MenuSchema, MenuCategory as MenuCategorySchema
from core.schemas.nutrition import Nutrition
from core.schemas.restaurant import Restaurant as RestaurantSchema
from core.schemas.restaurant import RestaurantLocation as RestaurantLocationSchema
from database.models.restaurant import Dish, Menu, MenuCategory, Restaurant, RestaurantLocation


class RestaurantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_full_tree(self, restaurant_id: uuid.UUID) -> RestaurantSchema | None:
        """Reads a published restaurant back out as the same
        core.schemas.restaurant.Restaurant shape persist_tree writes from
        — the read half of the production tables, used by the reviewer
        workflow's JSON Delta Generation node to diff a fresh re-crawl
        against what's actually live, not against a stale in-memory copy.
        Read-only; this method never writes."""
        result = await self._session.execute(
            select(Restaurant)
            .where(Restaurant.id == restaurant_id)
            .options(
                selectinload(Restaurant.locations),
                selectinload(Restaurant.menus).selectinload(Menu.categories).selectinload(MenuCategory.children),
                selectinload(Restaurant.menus).selectinload(Menu.categories).selectinload(MenuCategory.dishes),
            )
        )
        record = result.unique().scalar_one_or_none()
        if record is None:
            return None
        return self._to_schema(record)

    def _to_schema(self, record: Restaurant) -> RestaurantSchema:
        return RestaurantSchema(
            id=record.id,
            name=record.name,
            description=record.description,
            logo_url=record.logo_url,
            cover_image_url=record.cover_image_url,
            gallery_image_urls=list(record.gallery_image_urls),
            website_url=record.website_url,
            cuisine_types=list(record.cuisine_types),
            is_active=record.is_active,
            locations=[
                RestaurantLocationSchema(
                    id=location.id,
                    label=location.label,
                    address_line1=location.address_line1,
                    address_line2=location.address_line2,
                    city=location.city,
                    state=location.state,
                    postal_code=location.postal_code,
                    country=location.country,
                    latitude=float(location.latitude) if location.latitude is not None else None,
                    longitude=float(location.longitude) if location.longitude is not None else None,
                    phone=location.phone,
                )
                for location in record.locations
            ],
            menus=[
                MenuSchema(
                    id=menu.id,
                    name=menu.name,
                    categories=[
                        self._category_to_schema(category)
                        for category in menu.categories
                        if category.parent_id is None
                    ],
                )
                for menu in record.menus
            ],
        )

    def _category_to_schema(self, category: MenuCategory) -> MenuCategorySchema:
        return MenuCategorySchema(
            id=category.id,
            name=category.name,
            display_order=category.display_order,
            children=[self._category_to_schema(child) for child in category.children],
            dishes=[self._dish_to_schema(dish) for dish in category.dishes],
        )

    def _dish_to_schema(self, dish: Dish) -> DishSchema:
        return DishSchema(
            id=dish.id,
            category_id=dish.category_id,
            name=dish.name,
            description=dish.description,
            image_url=dish.image_url,
            nutrition=Nutrition.model_validate(dish.nutrition),
            allergens=list(dish.allergens),
            ingredients=[Ingredient.model_validate(ingredient) for ingredient in dish.ingredients],
            quantity=dish.quantity,
            price=dish.price,
            currency=dish.currency,
            review_state=dish.review_state,
        )

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
