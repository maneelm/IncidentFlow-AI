from fastapi import APIRouter

from app.api.v1 import (
    auth,
    dashboard,
    detection_jobs,
    detection_rules,
    detections,
    health,
    incidents,
    ingestion_credentials,
    investigations,
    logs,
    resolutions,
    runbooks,
    services,
)
from app.schemas.common import ErrorResponse

router = APIRouter(
    prefix="/api/v1",
    responses={
        code: {"model": ErrorResponse}
        for code in (401, 403, 404, 409, 413, 415, 422, 429, 500, 503)
    },
)
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(dashboard.router)
router.include_router(services.router)
router.include_router(incidents.router)
router.include_router(logs.router)
router.include_router(ingestion_credentials.router)
router.include_router(detection_rules.router)
router.include_router(detections.router)
router.include_router(detection_jobs.router)
router.include_router(investigations.router)
router.include_router(runbooks.router)
router.include_router(resolutions.router)
