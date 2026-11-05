from typing import Annotated

from fastapi import Depends

from app.api.dependencies import AppSettings, Session
from app.investigation.safety import Sanitizer
from app.services.investigations import InvestigationService
from app.services.runbooks import RunbookService


def investigations(session: Session, settings: AppSettings) -> InvestigationService:
    return InvestigationService(session, settings)


def runbooks(session: Session, settings: AppSettings) -> RunbookService:
    return RunbookService(session, Sanitizer.from_settings(settings))


Investigations = Annotated[InvestigationService, Depends(investigations)]
Runbooks = Annotated[RunbookService, Depends(runbooks)]
