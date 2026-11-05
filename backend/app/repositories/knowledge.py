import re
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.investigation.safety import Sanitizer
from app.models import DetectionOccurrence, Incident, IncidentEvent, IncidentStatus
from app.models.investigations import ResolutionNote, Runbook, RunbookChunk, RunbookVersion
from app.schemas.runbooks import SearchHit

CONFIG = literal_column("'simple'::regconfig")


class KnowledgeRepository:
    def __init__(self, session: AsyncSession, sanitizer: Sanitizer | None = None) -> None:
        self.session = session
        self.sanitizer = sanitizer or Sanitizer()

    async def runbooks(
        self, query: str, service_id: UUID | None, limit: int, as_of: datetime | None = None
    ) -> list[SearchHit]:
        tsquery = func.websearch_to_tsquery(CONFIG, query)
        vector = func.to_tsvector(CONFIG, RunbookChunk.search_text)
        score = func.ts_rank_cd(vector, tsquery)
        relevance = case((RunbookVersion.service_id == service_id, 0), else_=1)
        selection = (
            select(RunbookChunk, RunbookVersion, score)
            .join(RunbookVersion, RunbookVersion.id == RunbookChunk.version_id)
            .join(Runbook, Runbook.id == RunbookVersion.runbook_id)
            .where(vector.bool_op("@@")(tsquery))
        )
        if as_of:
            latest = (
                select(
                    RunbookVersion.runbook_id.label("runbook_id"),
                    func.max(RunbookVersion.version).label("version"),
                )
                .where(RunbookVersion.created_at <= as_of)
                .group_by(RunbookVersion.runbook_id)
                .subquery()
            )
            selection = selection.join(
                latest,
                (latest.c.runbook_id == RunbookVersion.runbook_id)
                & (latest.c.version == RunbookVersion.version),
            )
        else:
            selection = selection.where(RunbookVersion.version == Runbook.current_version)
        if service_id:
            selection = selection.where(
                or_(RunbookVersion.service_id == service_id, RunbookVersion.service_id.is_(None))
            )
        rows = (
            await self.session.execute(
                selection.order_by(
                    relevance, score.desc(), RunbookVersion.runbook_id, RunbookChunk.ordinal
                ).limit(limit)
            )
        ).all()
        return [
            SearchHit(
                source_type="runbook_chunk",
                source_id=chunk.id,
                title=version.title,
                excerpt=chunk.content[:600],
                score=float(rank),
                rank=index + 1,
                service_relevance="same_service" if version.service_id == service_id else "global",
                source_timestamp=version.created_at,
                document_version=version.version,
            )
            for index, (chunk, version, rank) in enumerate(rows)
        ]

    async def history(
        self,
        query: str,
        service_id: UUID | None,
        exclude: UUID | None,
        limit: int,
        as_of: datetime | None = None,
    ) -> list[SearchHit]:
        tsquery = func.websearch_to_tsquery(CONFIG, query)
        text = Incident.title + " " + Incident.description
        vector = func.to_tsvector(CONFIG, text)
        score = func.ts_rank_cd(vector, tsquery)
        relevance = case((Incident.service_id == service_id, 0), else_=1)
        codes = re.findall(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b", query.upper())[:8]
        detection_match = (
            select(DetectionOccurrence.id)
            .where(
                DetectionOccurrence.incident_id == Incident.id,
                DetectionOccurrence.rule_snapshot["error_code"].astext.in_(codes),
            )
            .exists()
        )
        query_stmt = select(Incident, score).where(
            Incident.status.in_([IncidentStatus.RESOLVED, IncidentStatus.CLOSED]),
            or_(vector.bool_op("@@")(tsquery), detection_match),
        )
        if exclude:
            query_stmt = query_stmt.where(Incident.id != exclude)
        if as_of:
            query_stmt = query_stmt.where(Incident.resolved_at <= as_of)
        rows = (
            await self.session.execute(
                query_stmt.order_by(
                    relevance, score.desc(), Incident.resolved_at.desc(), Incident.id
                ).limit(limit)
            )
        ).all()
        history_ids = [incident.id for incident, _ in rows]
        events = (
            list(
                await self.session.scalars(
                    select(IncidentEvent)
                    .where(
                        IncidentEvent.incident_id.in_(history_ids),
                        IncidentEvent.event_type == "transitioned",
                    )
                    .distinct(IncidentEvent.incident_id)
                    .order_by(IncidentEvent.incident_id, IncidentEvent.incident_version.desc())
                )
            )
            if history_ids
            else []
        )
        reasons = {event.incident_id: str(event.details.get("reason", "")) for event in events}
        detections = (
            list(
                await self.session.scalars(
                    select(DetectionOccurrence).where(
                        DetectionOccurrence.incident_id.in_(history_ids)
                    )
                )
            )
            if history_ids
            else []
        )
        codes = {item.incident_id: item.rule_snapshot.get("error_code", "") for item in detections}
        descriptions = {
            incident.id: self.sanitizer.text(incident.description, 300, quarantine=True)
            for incident, _ in rows
        }
        safe_reasons = {
            key: self.sanitizer.text(value, 180, quarantine=True) for key, value in reasons.items()
        }
        hits = [
            SearchHit(
                source_type="historical_incident",
                source_id=incident.id,
                title=incident.title,
                excerpt=(
                    f"{descriptions[incident.id]} Resolution history: "
                    f"{safe_reasons.get(incident.id, 'not recorded')}. "
                    f"Detection code: {codes.get(incident.id, 'none')}"
                )[:600],
                score=float(rank),
                rank=index + 1,
                service_relevance="same_service"
                if incident.service_id == service_id
                else "other_service",
                source_timestamp=incident.resolved_at,
                document_version=None,
            )
            for index, (incident, rank) in enumerate(rows)
        ]
        note_vector = func.to_tsvector(CONFIG, ResolutionNote.content)
        note_score = func.ts_rank_cd(note_vector, tsquery)
        notes = (
            select(ResolutionNote, Incident, note_score)
            .join(Incident)
            .where(
                Incident.status.in_([IncidentStatus.RESOLVED, IncidentStatus.CLOSED]),
                note_vector.bool_op("@@")(tsquery),
            )
        )
        if exclude:
            notes = notes.where(Incident.id != exclude)
        if as_of:
            notes = notes.where(ResolutionNote.created_at <= as_of)
        rows = (
            await self.session.execute(
                notes.order_by(
                    relevance,
                    note_score.desc(),
                    ResolutionNote.created_at.desc(),
                    ResolutionNote.id,
                ).limit(limit)
            )
        ).all()
        hits.extend(
            SearchHit(
                source_type="resolution_note",
                source_id=note.id,
                title=f"Resolution note: {incident.title}",
                excerpt=self.sanitizer.text(note.content, 600, quarantine=True),
                score=float(rank),
                rank=index + 1,
                service_relevance="same_service"
                if incident.service_id == service_id
                else "other_service",
                source_timestamp=note.created_at,
                document_version=None,
            )
            for index, (note, incident, rank) in enumerate(rows)
        )
        ranked = sorted(
            hits,
            key=lambda hit: (
                hit.service_relevance != "same_service",
                -hit.score,
                str(hit.source_id),
            ),
        )[:limit]
        return [hit.model_copy(update={"rank": index + 1}) for index, hit in enumerate(ranked)]
