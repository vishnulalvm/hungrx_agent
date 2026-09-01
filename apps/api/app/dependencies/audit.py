from typing import Annotated

from fastapi import Depends

from apps.api.app.dependencies.db import DbSessionDep
from apps.api.app.services.audit_service import AuditService


def get_audit_service(db: DbSessionDep) -> AuditService:
    return AuditService(db)


AuditServiceDep = Annotated[AuditService, Depends(get_audit_service)]
