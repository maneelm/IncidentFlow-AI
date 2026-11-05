from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import DomainError
from app.core.security import DUMMY_HASH, create_token, hash_password, verify_password
from app.models import User
from app.repositories.users import UserRepository
from app.schemas.auth import BootstrapInput, TokenRead


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.users = UserRepository(session)

    async def login(self, email: str, password: str) -> TokenRead:
        user = await self.users.by_email(email)
        valid = await run_in_threadpool(
            verify_password, password, user.password_hash if user else DUMMY_HASH
        )
        if user is None or not valid:
            raise DomainError(401, "unauthenticated", "Incorrect email or password")
        return TokenRead(
            access_token=create_token(user.id, self.settings),
            expires_in=self.settings.jwt_access_token_minutes * 60,
        )

    async def current_user(self, user_id: UUID) -> User:
        user = await self.users.get(user_id)
        if user is None:
            raise DomainError(401, "unauthenticated", "Invalid or expired access token")
        return user

    async def bootstrap(self, data: BootstrapInput) -> User:
        if await self.users.by_email(str(data.email)):
            raise DomainError(409, "duplicate_user", "User already exists; no changes made")
        user = User(
            email=str(data.email),
            password_hash=await run_in_threadpool(hash_password, data.password.get_secret_value()),
            display_name=data.display_name,
            role=data.role,
        )
        self.session.add(user)
        await self.session.flush()
        return user
