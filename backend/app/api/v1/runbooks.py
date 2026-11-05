from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, Response, UploadFile

from app.api.dependencies import Admin, CurrentUser, Writer
from app.api.investigation_dependencies import Runbooks
from app.schemas.common import Page
from app.schemas.investigations import InvestigationPage
from app.schemas.runbooks import (
    ChunkRead,
    KnowledgeSearch,
    NoteCreate,
    NoteRead,
    ReindexInput,
    RunbookCreate,
    RunbookDocument,
    RunbookRead,
    RunbookUpdate,
    SearchHit,
    VersionRead,
)

router = APIRouter(tags=["operational knowledge"])


@router.get("/runbook-chunks/{chunk_id}", response_model=ChunkRead)
async def chunk(chunk_id: UUID, service: Runbooks, user: CurrentUser) -> ChunkRead:
    return ChunkRead.model_validate(await service.chunk(chunk_id))


@router.get("/runbooks/{runbook_id}/versions/{version}", response_model=VersionRead)
async def version(
    runbook_id: UUID, version: int, service: Runbooks, user: CurrentUser
) -> VersionRead:
    return VersionRead.model_validate(await service.document_version(runbook_id, version))


@router.post("/incidents/{incident_id}/similar", response_model=list[SearchHit])
async def history(
    incident_id: UUID, data: KnowledgeSearch, service: Runbooks, user: CurrentUser
) -> list[SearchHit]:
    return await service.history(incident_id, data)


@router.post("/runbooks", response_model=RunbookDocument, status_code=201)
async def create(
    data: RunbookCreate, response: Response, service: Runbooks, actor: Admin
) -> RunbookDocument:
    result = await service.create(data, actor)
    response.headers["Location"] = f"/api/v1/runbooks/{result.id}"
    return result


@router.post("/runbooks/upload", response_model=RunbookDocument, status_code=201)
async def upload(
    service: Runbooks,
    actor: Admin,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1, max_length=200)],
    service_id: Annotated[UUID | None, Form()] = None,
) -> RunbookDocument:
    return await service.upload(file, title, service_id, actor)


@router.post("/runbooks/search", response_model=list[SearchHit])
async def search(data: KnowledgeSearch, service: Runbooks, user: CurrentUser) -> list[SearchHit]:
    return await service.search(data)


@router.get("/runbooks", response_model=Page[RunbookRead])
async def list_runbooks(
    filters: Annotated[InvestigationPage, Query()], service: Runbooks, user: CurrentUser
) -> Page[RunbookRead]:
    return await service.list(filters.limit, filters.offset)


@router.get("/runbooks/{runbook_id}", response_model=RunbookDocument)
async def get(runbook_id: UUID, service: Runbooks, user: CurrentUser) -> RunbookDocument:
    return await service.get(runbook_id)


@router.patch("/runbooks/{runbook_id}", response_model=RunbookDocument)
async def update(
    runbook_id: UUID, data: RunbookUpdate, service: Runbooks, actor: Admin
) -> RunbookDocument:
    return await service.update(runbook_id, data, actor)


@router.post("/runbooks/{runbook_id}/reindex", response_model=RunbookDocument)
async def reindex(
    runbook_id: UUID, data: ReindexInput, service: Runbooks, actor: Admin
) -> RunbookDocument:
    return await service.replace(runbook_id, data.expected_version, actor, {}, force=True)


@router.post("/incidents/{incident_id}/resolution-notes", response_model=NoteRead, status_code=201)
async def note(incident_id: UUID, data: NoteCreate, service: Runbooks, actor: Writer) -> NoteRead:
    return NoteRead.model_validate(await service.note(incident_id, data.content, actor))


@router.get("/incidents/{incident_id}/resolution-notes", response_model=Page[NoteRead])
async def notes(
    incident_id: UUID,
    filters: Annotated[InvestigationPage, Query()],
    service: Runbooks,
    user: CurrentUser,
) -> Page[NoteRead]:
    return await service.notes(incident_id, filters.limit, filters.offset)
