from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from pwdlib import PasswordHash

from app.core.config import Settings
from app.core.errors import DomainError

password_hasher = PasswordHash.recommended()
# Equalize the expensive password verification path for unknown emails.
DUMMY_HASH = password_hasher.hash("unused-timing-equalizer")


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    return password_hasher.verify(password, encoded)


def create_token(user_id: UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.jwt_access_token_minutes),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "type": "access",
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_token(token: str, settings: Settings) -> UUID:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "iat", "exp", "iss", "aud", "type"]},
        )
        if claims["type"] != "access":
            raise ValueError("Incorrect token type")
        return UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError):
        raise DomainError(401, "unauthenticated", "Invalid or expired access token") from None
