import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException

from app.api.v1.router import router
from app.core.config import Settings
from app.core.errors import DomainError, version_conflict
from app.core.payload_limit import IngestionPayloadLimit
from app.db.session import make_engine, make_session_factory

logger = logging.getLogger("incidentflow")


def error_response(
    status: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or []}},
        headers=headers,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    engine = make_engine(settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        logger.info("api_started")
        try:
            yield
        finally:
            await application.state.engine.dispose()
            logger.info("api_stopped")

    application = FastAPI(
        title="IncidentFlow AI",
        version="0.4.0",
        lifespan=lifespan,
        description=(
            "Incident detection, AI investigations and human-approved syntheti"
            "c resolution workflows."
        ),
    )
    application.state.settings = settings
    application.state.engine = engine
    application.state.session_factory = make_session_factory(engine)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT"],
        allow_headers=["Authorization", "Content-Type"],
    )
    application.add_middleware(
        IngestionPayloadLimit, max_bytes=settings.ingestion_max_request_bytes
    )

    @application.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        challenge = "ApiKey" if exc.code == "invalid_ingestion_credential" else "Bearer"
        headers = {"WWW-Authenticate": challenge} if exc.status == 401 else None
        return error_response(exc.status, exc.code, exc.message, headers=headers)

    async def validation_error(
        request: Request, exc: RequestValidationError | ValidationError
    ) -> JSONResponse:
        # Never echo user input, exception context, tokens, or password values.
        details = [
            {
                "location": list(error["loc"]),
                "type": error["type"],
                "message": "Invalid value for this field",
            }
            for error in exc.errors()
        ]
        return error_response(422, "validation_error", "Request validation failed", details)

    application.add_exception_handler(RequestValidationError, validation_error)
    application.add_exception_handler(ValidationError, validation_error)

    @application.exception_handler(StaleDataError)
    async def stale_error(request: Request, exc: StaleDataError) -> JSONResponse:
        conflict = version_conflict()
        return error_response(conflict.status, conflict.code, conflict.message)

    @application.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        # Driver exceptions can contain SQL values; neither render nor log them.
        return error_response(
            409, "constraint_conflict", "Operation conflicts with database constraints"
        )

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error_response(exc.status_code, "http_error", str(exc.detail), headers=exc.headers)

    @application.middleware("http")
    async def protect_error_details(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Catch before ServerErrorMiddleware re-raises to Uvicorn's traceback logger.
        # Unexpected exception text can contain credentials supplied to a driver.
        try:
            response = await call_next(request)
            if request.url.path.startswith("/api/"):
                response.headers["Cache-Control"] = "no-store"
                response.headers["Pragma"] = "no-cache"
            return response
        except Exception as exc:
            logger.error("Request failed (%s)", type(exc).__name__)
            return error_response(500, "internal_error", "An unexpected error occurred")

    application.include_router(router)
    return application
