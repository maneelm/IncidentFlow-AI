from typing import Annotated

from fastapi import Depends
from fastapi.security import APIKeyHeader

from app.api.dependencies import AppSettings, Session
from app.models import IngestionCredential
from app.services.detection_queries import DetectionQueries
from app.services.detection_rules import RuleService
from app.services.ingestion_credentials import CredentialService
from app.services.log_ingestion import LogService

ingestion_key = APIKeyHeader(name="X-Ingestion-Key", auto_error=False)


def credential_service(session: Session) -> CredentialService:
    return CredentialService(session)


Credentials = Annotated[CredentialService, Depends(credential_service)]


async def ingestion_credential(
    token: Annotated[str | None, Depends(ingestion_key)], service: Credentials
) -> IngestionCredential:
    return await service.authenticate(token)


Machine = Annotated[IngestionCredential, Depends(ingestion_credential)]


def log_service(session: Session, settings: AppSettings) -> LogService:
    return LogService(session, settings)


def rule_service(session: Session) -> RuleService:
    return RuleService(session)


def detection_queries(session: Session) -> DetectionQueries:
    return DetectionQueries(session)


Logs = Annotated[LogService, Depends(log_service)]
Rules = Annotated[RuleService, Depends(rule_service)]
Detections = Annotated[DetectionQueries, Depends(detection_queries)]
