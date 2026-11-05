from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.dependencies import Admin, CurrentUser
from app.api.observability_dependencies import Rules
from app.schemas.common import Page
from app.schemas.detection import RuleCreate, RuleFilters, RuleRead, RuleUpdate

router = APIRouter(prefix="/detection-rules", tags=["detection rules"])


@router.post("", response_model=RuleRead, status_code=201)
async def create_rule(
    data: RuleCreate, response: Response, service: Rules, actor: Admin
) -> RuleRead:
    rule = await service.create(data)
    response.headers["Location"] = f"/api/v1/detection-rules/{rule.id}"
    return RuleRead.model_validate(rule)


@router.get("", response_model=Page[RuleRead])
async def list_rules(
    filters: Annotated[RuleFilters, Query()], service: Rules, user: CurrentUser
) -> Page[RuleRead]:
    return await service.list(filters.limit, filters.offset, filters.service_id, filters.enabled)


@router.get("/{rule_id}", response_model=RuleRead)
async def get_rule(rule_id: UUID, service: Rules, user: CurrentUser) -> RuleRead:
    return RuleRead.model_validate(await service.get(rule_id))


@router.patch("/{rule_id}", response_model=RuleRead)
async def update_rule(rule_id: UUID, data: RuleUpdate, service: Rules, actor: Admin) -> RuleRead:
    return RuleRead.model_validate(await service.update(rule_id, data))
