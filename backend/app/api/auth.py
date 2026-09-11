from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.rate_limit import per_ip_rate_limit
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth import Token, UserCreate, UserLogin, UserRead
from app.schemas.errors import error_response

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(per_ip_rate_limit("auth_register"))],
    summary="Register a new user",
    description="Creates a user account. Passwords are hashed with bcrypt before storage.",
    responses={
        400: error_response("Email is already registered."),
        429: error_response("Too many registration attempts from this IP."),
    },
)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post(
    "/login",
    response_model=Token,
    dependencies=[Depends(per_ip_rate_limit("auth_login"))],
    summary="Log in and obtain an access token",
    description=(
        "Exchanges an email/password pair for a JWT bearer token. Send the "
        "token on subsequent requests as `Authorization: Bearer <token>`."
    ),
    responses={
        401: error_response("Incorrect email or password."),
        403: error_response("The account exists but is inactive."),
        429: error_response("Too many login attempts from this IP."),
    },
)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")

    access_token = create_access_token(user.id)
    return Token(access_token=access_token)


@router.get(
    "/me",
    response_model=UserRead,
    summary="Get the current authenticated user",
    responses={401: error_response("Missing, invalid, or expired access token.")},
)
async def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user
