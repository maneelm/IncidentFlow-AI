import base64
import json
from datetime import datetime
from uuid import UUID

from pydantic import Field, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError, not_found
from app.db.clock import database_now
from app.models import Incident
from app.schemas.common import InputModel


class TimelineItem(InputModel):
    occurred_at: datetime
    source_type: str
    source_id: UUID
    event_type: str
    details: dict


class TimelineCursor(InputModel):
    incident_id: UUID
    until: datetime
    last_time: datetime
    last_type: str = Field(max_length=30)
    last_id: UUID
    last_event: str = Field(max_length=50)


class TimelinePage(InputModel):
    items: list[TimelineItem]
    next_cursor: str | None
    until: datetime


TIMELINE_SQL = """
WITH activity AS (
    SELECT occurred_at, 'incident_event' AS source_type, id AS source_id, event_type,
        jsonb_build_object('incident_version',incident_version,
            'actor_id',actor_id,'system_actor',system_actor) AS details
    FROM incident_events WHERE incident_id=:incident
    UNION ALL
    SELECT created_at,'detection',id,'detection_created',
        jsonb_build_object('rule_id',rule_id,'service_id',service_id)
    FROM detection_occurrences WHERE incident_id=:incident
    UNION ALL
    SELECT created_at,'investigation',id,'investigation_requested',
        jsonb_build_object('requested_by',requested_by)
    FROM investigations WHERE incident_id=:incident
    UNION ALL
    SELECT started_at,'investigation',id,'investigation_started','{}'::jsonb
    FROM investigations WHERE incident_id=:incident AND started_at IS NOT NULL
    UNION ALL
    SELECT completed_at,'investigation',id,'investigation_'||status,
        jsonb_build_object('error_code',error_code)
    FROM investigations WHERE incident_id=:incident AND completed_at IS NOT NULL
    UNION ALL
    SELECT e.occurred_at,'resolution_event',e.id,e.event_type,
        jsonb_build_object('proposal_id',e.proposal_id,'proposal_version',e.proposal_version,
            'actor_id',e.actor_id,'system_actor',e.system_actor,
            'correlation_id',e.correlation_id,'details',e.details)
    FROM resolution_events e JOIN resolution_proposals p ON e.proposal_id=p.id
    WHERE p.incident_id=:incident
)
SELECT * FROM activity WHERE occurred_at<=:until
AND (:first OR (occurred_at,source_type,source_id,event_type) >
    (:last_time,:last_type,CAST(:last_id AS uuid),:last_event))
ORDER BY occurred_at,source_type,source_id,event_type LIMIT :limit
"""


async def incident_timeline(
    session: AsyncSession, incident_id: UUID, limit: int, cursor: str | None
) -> TimelinePage:
    if await session.get(Incident, incident_id) is None:
        raise not_found("Incident")
    until = await database_now(session)
    position = None
    if cursor:
        try:
            position = TimelineCursor.model_validate_json(base64.urlsafe_b64decode(cursor.encode()))
            if (
                position.incident_id != incident_id
                or position.until.tzinfo is None
                or position.last_time.tzinfo is None
                or position.last_time > position.until
            ):
                raise ValueError
            until = position.until
        except (ValueError, ValidationError, UnicodeError):
            raise DomainError(422, "invalid_cursor", "Invalid incident timeline cursor") from None
    rows = (
        (
            await session.execute(
                text(TIMELINE_SQL),
                {
                    "incident": incident_id,
                    "until": until,
                    "first": position is None,
                    "last_time": position.last_time if position else until,
                    "last_type": position.last_type if position else "",
                    "last_id": position.last_id if position else incident_id,
                    "last_event": position.last_event if position else "",
                    "limit": limit + 1,
                },
            )
        )
        .mappings()
        .all()
    )
    items = [TimelineItem.model_validate(dict(row)) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                TimelineCursor(
                    incident_id=incident_id,
                    until=until,
                    last_time=last.occurred_at,
                    last_type=last.source_type,
                    last_id=last.source_id,
                    last_event=last.event_type,
                ).model_dump(mode="json")
            ).encode()
        ).decode()
    return TimelinePage(items=items, next_cursor=next_cursor, until=until)
