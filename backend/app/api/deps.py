from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import User

# `tokenUrl` only feeds Swagger UI's "Authorize" button and OpenAPI's
# security-scheme metadata - it doesn't validate or issue tokens itself.
# Note POST /auth/login actually takes a JSON body (UserLogin), not the
# OAuth2 form-encoded username/password this class's Authorize dialog
# submits, so the dialog itself won't complete a real login; paste an
# already-obtained token into the dialog's "Value" field instead - the
# `description` below documents that for anyone using Swagger directly.
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/login",
    description=(
        "JWT access token obtained from POST /auth/login. "
        "Send as: Authorization: Bearer <token>"
    ),
)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    user_id = decode_access_token(token)
    if user_id is None:
        raise credentials_error

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise credentials_error

    return user
