import hashlib
import re
from datetime import timedelta
from hmac import compare_digest
from secrets import token_urlsafe
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError, not_found
from app.db.clock import database_now
from app.models import IngestionCredential, Service, User
from app.repositories.detection import DetectionRepository
from app.schemas.common import Page
from app.schemas.detection import CredentialCreate, CredentialIssued, CredentialRead


class CredentialService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, data: CredentialCreate, actor: User) -> CredentialIssued:
        if await self.session.get(Service, data.service_id) is None:
            raise not_found("Service")
        now = await database_now(self.session)
        credential_id = uuid4()
        token = f"iflow_{credential_id.hex}.{token_urlsafe(32)}"
        credential = IngestionCredential(
            id=credential_id,
            service_id=data.service_id,
            name=data.name,
            created_by=actor.id,
            secret_hash=hashlib.sha256(token.encode()).hexdigest(),
            created_at=now,
            expires_at=now + timedelta(days=data.expires_in_days),
        )
        self.session.add(credential)
        await self.session.flush()
        return CredentialIssued(
            **CredentialRead.model_validate(credential).model_dump(), token=token
        )

    async def authenticate(self, token: str | None) -> IngestionCredential:
        match = re.fullmatch(r"iflow_([0-9a-f]{32})\.[A-Za-z0-9_-]{43}", token or "")
        if match:
            # SHARE prevents revocation from committing during an authorized ingestion.
            credential = await self.session.scalar(
                select(IngestionCredential)
                .where(IngestionCredential.id == UUID(hex=match[1]))
                .with_for_update(read=True)
            )
            digest = hashlib.sha256(token.encode()).hexdigest()
            if credential is not None and compare_digest(credential.secret_hash, digest):
                now = await database_now(self.session)
                if credential.revoked_at is None and credential.expires_at > now:
                    return credential
        raise DomainError(
            401, "invalid_ingestion_credential", "Valid service ingestion credentials are required"
        )

    async def get(self, credential_id: UUID) -> IngestionCredential:
        credential = await self.session.get(IngestionCredential, credential_id)
        if credential is None:
            raise not_found("Ingestion credential")
        return credential

    async def revoke(self, credential_id: UUID) -> IngestionCredential:
        credential = await self.session.scalar(
            select(IngestionCredential)
            .where(IngestionCredential.id == credential_id)
            .with_for_update()
        )
        if credential is None:
            raise not_found("Ingestion credential")
        if credential.revoked_at is None:
            credential.revoked_at = await database_now(self.session)
            await self.session.flush()
        return credential

    async def list(self, limit: int, offset: int, service_id: UUID | None) -> Page[CredentialRead]:
        items, total = await DetectionRepository(self.session).page(
            IngestionCredential, limit, offset, service_id=service_id
        )
        return Page[CredentialRead](items=items, total=total, limit=limit, offset=offset)
