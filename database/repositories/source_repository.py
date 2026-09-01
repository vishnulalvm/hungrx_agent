import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.source import SourceType
from database.models.source import Source


class SourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, restaurant_id: uuid.UUID, source_type: SourceType, url: str, is_verified_domain: bool
    ) -> Source:
        record = Source(
            restaurant_id=restaurant_id,
            source_type=source_type,
            url=url,
            is_verified_domain=is_verified_domain,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_by_id(self, source_id: uuid.UUID) -> Source | None:
        return await self._session.get(Source, source_id)

    async def get_verified_website_for_restaurant(self, restaurant_id: uuid.UUID) -> Source | None:
        result = await self._session.execute(
            select(Source)
            .where(
                Source.restaurant_id == restaurant_id,
                Source.source_type == SourceType.RESTAURANT_WEBSITE,
                Source.is_verified_domain.is_(True),
            )
            .order_by(desc(Source.created_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_for_restaurant(self, restaurant_id: uuid.UUID) -> list[Source]:
        result = await self._session.execute(
            select(Source)
            .where(Source.restaurant_id == restaurant_id)
            .order_by(desc(Source.created_at))
        )
        return list(result.scalars().all())
