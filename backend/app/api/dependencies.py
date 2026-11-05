from collections.abc import AsyncIterator, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError
from app.core.security import decode_token
from app.models import Role, User
from app.services.auth import AuthService
from app.services.catalog import CatalogService
from app.services.incidents import IncidentService

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session, session.begin():
        yield session


# Commit/rollback before sending the response so commit errors cannot become false successes.
Session = Annotated[AsyncSession, Depends(get_session, scope="function")]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


AppSettings = Annotated[Settings, Depends(get_settings)]


def auth_service(session: Session, settings: AppSettings) -> AuthService:
    return AuthService(session, settings)


Auth = Annotated[AuthService, Depends(auth_service)]


async def current_user(
    token: Annotated[str | None, Depends(oauth2)], settings: AppSettings, service: Auth
) -> User:
    if token is None:
        raise DomainError(401, "unauthenticated", "Bearer authentication is required")
    return await service.current_user(decode_token(token, settings))


CurrentUser = Annotated[User, Depends(current_user)]


def require_roles(*roles: Role) -> Callable:
    async def check(user: CurrentUser) -> User:
        if user.role not in roles:
            raise DomainError(403, "forbidden", "Your role does not allow this operation")
        return user

    return check


Writer = Annotated[User, Depends(require_roles(Role.ADMIN, Role.ENGINEER))]
Admin = Annotated[User, Depends(require_roles(Role.ADMIN))]


def catalog_service(session: Session) -> CatalogService:
    return CatalogService(session)


def incident_service(session: Session) -> IncidentService:
    return IncidentService(session)


Catalog = Annotated[CatalogService, Depends(catalog_service)]
Incidents = Annotated[IncidentService, Depends(incident_service)]
