from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.investigation.safety import INJECTION, Sanitizer
from app.models import (
    DetectionEvidence,
    DetectionOccurrence,
    Incident,
    IncidentEvent,
    LogEntry,
    Service,
)
from app.models.investigations import Investigation, InvestigationEvidence

SOURCE_COLUMNS = {
    "incident": "incident_id",
    "historical_incident": "incident_id",
    "service": "service_id",
    "event": "event_id",
    "log": "log_id",
    "detection": "detection_id",
    "runbook_chunk": "chunk_id",
    "resolution_note": "note_id",
}


def evidence_row(
    investigation_id: UUID,
    kind: str,
    source_id: UUID,
    label: str,
    excerpt: str,
    role: str,
    provenance: dict[str, Any],
    sanitizer: Sanitizer,
) -> InvestigationEvidence:
    quarantined = bool(
        INJECTION.search(excerpt) or INJECTION.search(label) or "[QUARANTINED:" in excerpt
    )
    return InvestigationEvidence(
        investigation_id=investigation_id,
        reference=f"{kind}:{source_id}",
        source_type=kind,
        **{SOURCE_COLUMNS[kind]: source_id},
        label=sanitizer.text(label, 240, quarantine=True),
        excerpt=sanitizer.text(excerpt, 600, quarantine=True),
        role=role,
        provenance=sanitizer.data(provenance),
        quarantined=quarantined,
    )


class EvidenceRepository:
    def __init__(self, session: AsyncSession, sanitizer: Sanitizer) -> None:
        self.session = session
        self.sanitizer = sanitizer

    async def context(
        self, investigation: Investigation
    ) -> tuple[dict, list[InvestigationEvidence]]:
        incident = await self.session.get(Incident, investigation.incident_id)
        service = await self.session.get(Service, incident.service_id)
        occurrence = await self.session.scalar(
            select(DetectionOccurrence).where(DetectionOccurrence.incident_id == incident.id)
        )
        events = list(
            await self.session.scalars(
                select(IncidentEvent)
                .where(
                    IncidentEvent.incident_id == incident.id,
                    IncidentEvent.occurred_at <= investigation.created_at,
                )
                .order_by(IncidentEvent.incident_version.desc())
                .limit(8)
            )
        )
        context = self.sanitizer.data(
            {
                "incident_id": str(incident.id),
                "title": incident.title,
                "description": self.sanitizer.text(incident.description, 600, quarantine=True),
                "incident_version": incident.version,
                "status": incident.status,
                "severity": incident.severity,
                "service_id": str(service.id),
                "service_name": service.name,
                "assignee_id": str(incident.assignee_id) if incident.assignee_id else None,
                "created_at": incident.created_at.isoformat(),
                "requested_at": investigation.created_at.isoformat(),
                "detection": None
                if occurrence is None
                else {
                    "id": str(occurrence.id),
                    "window_start": occurrence.window_start.isoformat(),
                    "window_end": occurrence.window_end.isoformat(),
                    "matching_count": occurrence.matching_count,
                    "rule_id": str(occurrence.rule_id),
                    "rule_snapshot": occurrence.rule_snapshot,
                },
            }
        )
        sources = [
            self.make(
                investigation.id,
                "incident",
                incident.id,
                incident.title,
                incident.description,
                "incident_context",
                {"version": incident.version},
            ),
            self.make(
                investigation.id,
                "service",
                service.id,
                service.name,
                service.description,
                "service_context",
                {"environment": service.environment},
            ),
        ]
        if occurrence:
            sources.append(
                self.make(
                    investigation.id,
                    "detection",
                    occurrence.id,
                    "Detection trigger",
                    f"At least {occurrence.matching_count} logs matched the stored rule window.",
                    "detection_context",
                    context["detection"],
                )
            )
        for event in reversed(events):
            reason = event.details.get("reason", "")
            sources.append(
                self.make(
                    investigation.id,
                    "event",
                    event.id,
                    f"Incident event {event.incident_version}: {event.event_type}",
                    f"{event.event_type}. {reason}",
                    "audit_history",
                    {
                        "event_type": event.event_type,
                        "incident_version": event.incident_version,
                        "timestamp": event.occurred_at.isoformat(),
                    },
                )
            )
        return context, sources

    def make(
        self,
        investigation_id: UUID,
        kind: str,
        source_id: UUID,
        label: str,
        excerpt: str,
        role: str,
        provenance: dict,
    ) -> InvestigationEvidence:
        return evidence_row(
            investigation_id, kind, source_id, label, excerpt, role, provenance, self.sanitizer
        )

    async def logs(
        self, investigation: Investigation, context: dict
    ) -> list[InvestigationEvidence]:
        max_logs = investigation.policy["max_logs"]
        incident_service = UUID(context["service_id"])
        requested_at = investigation.created_at
        occurrence_data = context["detection"]
        occurrence = (
            await self.session.get(DetectionOccurrence, UUID(occurrence_data["id"]))
            if occurrence_data
            else None
        )
        anchor = occurrence.window_end if occurrence else investigation.created_at
        if occurrence is None:
            incident = await self.session.get(Incident, investigation.incident_id)
            anchor = incident.created_at
        start = (occurrence.window_start if occurrence else anchor) - timedelta(seconds=300)
        end = min(requested_at, anchor + timedelta(seconds=900))
        selected: list[tuple[LogEntry, str]] = []
        all_witnesses: list[UUID] = []
        if occurrence:
            snapshot = occurrence.rule_snapshot
            query = (
                select(LogEntry.id)
                .join(DetectionEvidence, DetectionEvidence.log_id == LogEntry.id)
                .where(
                    DetectionEvidence.occurrence_id == occurrence.id,
                    LogEntry.ingested_at > occurrence.window_start,
                    LogEntry.ingested_at <= occurrence.window_end,
                    LogEntry.level == snapshot["level"],
                    LogEntry.service_id == occurrence.service_id,
                )
            )
            if snapshot.get("error_code"):
                query = query.where(LogEntry.error_code == snapshot["error_code"])
            all_witnesses = list(
                await self.session.scalars(
                    query.order_by(
                        LogEntry.ingested_at, LogEntry.source_event_id, LogEntry.id
                    ).limit(occurrence.matching_count)
                )
            )
            witness_ids = all_witnesses[: (max_logs + 1) // 2]
            logs = list(
                await self.session.scalars(
                    select(LogEntry)
                    .where(LogEntry.id.in_(witness_ids))
                    .order_by(LogEntry.ingested_at, LogEntry.source_event_id, LogEntry.id)
                )
            )
            selected.extend((log, "original_witness") for log in logs)
            linked = list(
                await self.session.scalars(
                    select(LogEntry)
                    .join(DetectionEvidence, DetectionEvidence.log_id == LogEntry.id)
                    .where(
                        DetectionEvidence.occurrence_id == occurrence.id,
                        LogEntry.id.not_in(all_witnesses),
                        LogEntry.ingested_at.between(start, end),
                    )
                    .order_by(LogEntry.ingested_at.desc(), LogEntry.id)
                    .limit(max_logs // 4)
                )
            )
            selected.extend((log, "linked_evidence") for log in linked)
        ids = [log.id for log, _ in selected]
        traces = {log.trace_id for log, _ in selected if log.trace_id}
        requests = {log.request_id for log, _ in selected if log.request_id}
        errors = {log.error_code for log, _ in selected if log.error_code}
        rank = case(
            (LogEntry.trace_id.in_(traces), 0),
            (LogEntry.request_id.in_(requests), 1),
            (LogEntry.error_code.in_(errors), 2),
            else_=3,
        )
        contextual = list(
            await self.session.scalars(
                select(LogEntry)
                .where(
                    or_(LogEntry.service_id == incident_service, LogEntry.trace_id.in_(traces)),
                    LogEntry.ingested_at.between(start, end),
                    LogEntry.id.not_in(ids + all_witnesses),
                )
                .order_by(rank, LogEntry.ingested_at.desc(), LogEntry.id)
                .limit(max_logs - len(selected))
            )
        )
        selected.extend((log, "context_log") for log in contextual)
        return [
            self.make(
                investigation.id,
                "log",
                log.id,
                f"{log.level}: {log.error_code or 'no code'}",
                log.message,
                role,
                {
                    "service_id": str(log.service_id),
                    "level": log.level,
                    "error_code": log.error_code,
                    "trace_id": log.trace_id,
                    "request_id": log.request_id,
                    "source_timestamp": log.timestamp.isoformat(),
                    "ingested_at": log.ingested_at.isoformat(),
                    "metadata": {
                        key: value
                        for key, value in log.details.items()
                        if key
                        in {
                            "synthetic",
                            "scenario",
                            "latency_ms",
                            "breach_threshold_ms",
                            "http_status",
                            "pool_size",
                            "connections_in_use",
                        }
                    },
                    "collection_start": start.isoformat(),
                    "collection_end": end.isoformat(),
                },
            )
            for log, role in selected
        ]
