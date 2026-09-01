from typing import Annotated

from fastapi import Depends

from apps.api.app.dependencies.db import DbSessionDep
from apps.api.app.dependencies.settings import SettingsDep
from apps.api.app.services.review_service import ReviewService


def get_review_service(db: DbSessionDep, settings: SettingsDep) -> ReviewService:
    return ReviewService(db, settings)


ReviewServiceDep = Annotated[ReviewService, Depends(get_review_service)]
