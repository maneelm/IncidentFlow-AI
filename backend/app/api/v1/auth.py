from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.security import OAuth2PasswordRequestForm

from app.api.dependencies import Auth, CurrentUser
from app.schemas.auth import LoginInput, TokenRead, UserRead

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/token", response_model=TokenRead)
async def token(
    response: Response, form: Annotated[OAuth2PasswordRequestForm, Depends()], service: Auth
) -> TokenRead:
    data = LoginInput(username=form.username, password=form.password)
    result = await service.login(str(data.username), data.password.get_secret_value())
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return result


@router.get("/me", response_model=UserRead)
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
