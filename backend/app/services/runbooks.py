from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError, not_found
from app.investigation.safety import Sanitizer
from app.models import Incident, IncidentStatus, Service, User
from app.models.investigations import ResolutionNote, Runbook, RunbookChunk, RunbookVersion
from app.repositories.knowledge import KnowledgeRepository
from app.schemas.common import Page
from app.schemas.runbooks import (
    KnowledgeSearch,
    NoteRead,
    RunbookCreate,
    RunbookDocument,
    RunbookRead,
    RunbookUpdate,
    SearchHit,
)


def chunks(content: str) -> list[tuple[int, int, str]]:
    result = []
    start = 0
    while start < len(content):
        end = min(start + 1800, len(content))
        result.append((start, end, content[start:end]))
        if end == len(content):
            break
        start = end - 200
    return result


class RunbookService:
    def __init__(self, session: AsyncSession, sanitizer: Sanitizer) -> None:
        self.session = session
        self.sanitizer = sanitizer

    async def chunk(self, chunk_id: UUID) -> RunbookChunk:
        chunk = await self.session.get(RunbookChunk, chunk_id)
        if chunk is None:
            raise not_found("Runbook chunk")
        return chunk

    async def document_version(self, runbook_id: UUID, version: int) -> RunbookVersion:
        item = await self.session.scalar(
            select(RunbookVersion).where(
                RunbookVersion.runbook_id == runbook_id, RunbookVersion.version == version
            )
        )
        if item is None:
            raise not_found("Runbook version")
        return item

    async def create(self, data: RunbookCreate, actor: User) -> RunbookDocument:
        if data.service_id and await self.session.get(Service, data.service_id) is None:
            raise not_found("Service")
        document = Runbook(
            title=self.sanitizer.text(data.title, 200),
            service_id=data.service_id,
            tags=[self.sanitizer.text(tag, 40) for tag in data.tags],
            current_version=1,
        )
        self.session.add(document)
        await self.session.flush()
        await self.version(document, data.content, data.format, actor)
        return await self.get(document.id)

    async def version(self, document: Runbook, content: str, format: str, actor: User) -> None:
        content = self.sanitizer.text(content, 64000)
        if not content.strip():
            raise DomainError(422, "empty_runbook", "Runbook has no usable text")
        version = RunbookVersion(
            runbook_id=document.id,
            version=document.current_version,
            title=document.title,
            service_id=document.service_id,
            tags=document.tags,
            format=format,
            content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            created_by=actor.id,
        )
        self.session.add(version)
        await self.session.flush()
        for ordinal, (start, end, part) in enumerate(chunks(content)):
            self.session.add(
                RunbookChunk(
                    version_id=version.id,
                    ordinal=ordinal,
                    content=part,
                    start_char=start,
                    end_char=end,
                    content_hash=hashlib.sha256(part.encode()).hexdigest(),
                    search_text=f"{document.title} {' '.join(document.tags)} {part}",
                )
            )
        await self.session.flush()

    async def get(self, document_id: UUID) -> RunbookDocument:
        document = await self.session.get(Runbook, document_id)
        if document is None:
            raise not_found("Runbook")
        version = await self.session.scalar(
            select(RunbookVersion).where(
                RunbookVersion.runbook_id == document.id,
                RunbookVersion.version == document.current_version,
            )
        )
        ids = list(
            await self.session.scalars(
                select(RunbookChunk.id)
                .where(RunbookChunk.version_id == version.id)
                .order_by(RunbookChunk.ordinal)
            )
        )
        return RunbookDocument(
            **RunbookRead.model_validate(document).model_dump(),
            format=version.format,
            content=version.content,
            content_hash=version.content_hash,
            chunk_ids=ids,
        )

    async def update(self, document_id: UUID, data: RunbookUpdate, actor: User) -> RunbookDocument:
        return await self.replace(
            document_id,
            data.expected_version,
            actor,
            data.model_dump(exclude_unset=True, exclude={"expected_version"}),
        )

    async def replace(
        self,
        document_id: UUID,
        expected_version: int,
        actor: User,
        changes: dict,
        *,
        force: bool = False,
    ) -> RunbookDocument:
        document = await self.session.scalar(
            select(Runbook).where(Runbook.id == document_id).with_for_update()
        )
        if document is None:
            raise not_found("Runbook")
        if document.current_version != expected_version:
            raise DomainError(409, "version_conflict", "Runbook changed; retrieve it and retry")
        previous = await self.get(document_id)
        merged = RunbookCreate(
            **{
                key: changes.get(key, getattr(previous, key))
                for key in ("title", "service_id", "tags", "format", "content")
            }
        )
        if not force and all(getattr(previous, key) == getattr(merged, key) for key in changes):
            raise DomainError(409, "no_change", "Runbook already has the requested content")
        document.title = self.sanitizer.text(merged.title, 200)
        document.tags = [self.sanitizer.text(tag, 40) for tag in merged.tags]
        document.current_version += 1
        await self.version(document, merged.content, merged.format, actor)
        await self.session.refresh(document)
        return await self.get(document_id)

    async def upload(
        self, file: UploadFile, title: str, service_id: UUID | None, actor: User
    ) -> RunbookDocument:
        suffix = (file.filename or "").lower().rsplit(".", 1)[-1]
        if suffix not in {"md", "txt"} or file.content_type not in {
            "text/plain",
            "text/markdown",
            "application/octet-stream",
        }:
            raise DomainError(422, "invalid_document_format", "Upload UTF-8 .md or .txt files")
        body = await file.read(131073)
        if len(body) > 131072:
            raise DomainError(413, "payload_too_large", "Runbook exceeds the upload limit")
        try:
            content = body.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise DomainError(
                422, "invalid_document_encoding", "Runbooks must be UTF-8 text"
            ) from None
        return await self.create(
            RunbookCreate(
                title=title,
                service_id=service_id,
                content=content,
                format="markdown" if suffix == "md" else "text",
            ),
            actor,
        )

    async def list(self, limit: int, offset: int) -> Page[RunbookRead]:
        total = await self.session.scalar(select(func.count()).select_from(Runbook))
        items = await self.session.scalars(
            select(Runbook)
            .order_by(Runbook.updated_at.desc(), Runbook.id)
            .limit(limit)
            .offset(offset)
        )
        return Page[RunbookRead](items=list(items), total=total or 0, limit=limit, offset=offset)

    async def search(self, data: KnowledgeSearch) -> list[SearchHit]:
        return await KnowledgeRepository(self.session).runbooks(
            data.query, data.service_id, data.limit
        )

    async def history(self, incident_id: UUID, data: KnowledgeSearch) -> list[SearchHit]:
        incident = await self.session.get(Incident, incident_id)
        if incident is None:
            raise not_found("Incident")
        hits = await KnowledgeRepository(self.session, self.sanitizer).history(
            data.query, data.service_id or incident.service_id, incident_id, data.limit
        )
        return [
            hit.model_copy(
                update={
                    "title": self.sanitizer.text(hit.title, 240, quarantine=True),
                    "excerpt": self.sanitizer.text(hit.excerpt, 600, quarantine=True),
                }
            )
            for hit in hits
        ]

    async def note(self, incident_id: UUID, content: str, actor: User) -> ResolutionNote:
        incident = await self.session.get(Incident, incident_id)
        if incident is None:
            raise not_found("Incident")
        if incident.status not in {IncidentStatus.RESOLVED, IncidentStatus.CLOSED}:
            raise DomainError(
                409,
                "incident_not_resolved",
                "Resolution notes require a resolved or closed incident",
            )
        note = ResolutionNote(
            incident_id=incident_id, content=self.sanitizer.text(content, 8000), created_by=actor.id
        )
        self.session.add(note)
        await self.session.flush()
        return note

    async def notes(self, incident_id: UUID, limit: int, offset: int) -> Page[NoteRead]:
        if await self.session.get(Incident, incident_id) is None:
            raise not_found("Incident")
        predicate = ResolutionNote.incident_id == incident_id
        total = await self.session.scalar(
            select(func.count()).select_from(ResolutionNote).where(predicate)
        )
        items = await self.session.scalars(
            select(ResolutionNote)
            .where(predicate)
            .order_by(ResolutionNote.created_at, ResolutionNote.id)
            .limit(limit)
            .offset(offset)
        )
        return Page[NoteRead](items=list(items), total=total or 0, limit=limit, offset=offset)
