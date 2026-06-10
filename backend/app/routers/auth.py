from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.database import get_db
from app.repositories.user_repo import UserRepository
from app.utils.security import verify_password, create_access_token, decode_access_token
from pydantic import BaseModel, EmailStr
from typing import Optional
import secrets

router = APIRouter(prefix="/auth", tags=["Authentication"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
# Same scheme but does not auto-raise when the Authorization header is missing,
# so endpoints can fall back to the shared public user for no-login access.
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

# Shared account used for public (no-login) watchlists and portfolios.
PUBLIC_USER_EMAIL = "public@nse.local"

class UserRegister(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class UserResponse(BaseModel):
    id: int
    email: str
    is_active: bool

    class Config:
        from_attributes = True

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> UserResponse:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    user_id = decode_access_token(token)
    if user_id is None:
        raise credentials_exception
    
    user_repo = UserRepository(db)
    user = user_repo.get_by_id(int(user_id))
    if user is None:
        raise credentials_exception
    return user

def get_or_create_public_user(db: Session) -> UserResponse:
    """Return the shared public account, creating it on first use."""
    repo = UserRepository(db)
    user = repo.get_by_email(PUBLIC_USER_EMAIL)
    if user is None:
        user = repo.create(PUBLIC_USER_EMAIL, secrets.token_urlsafe(24))
    return user

def get_optional_user(
    token: Optional[str] = Depends(oauth2_scheme_optional),
    db: Session = Depends(get_db),
) -> UserResponse:
    """
    Use the authenticated user when a valid token is supplied; otherwise fall
    back to the shared public account so watchlists/portfolios work without login.
    """
    if token:
        user_id = decode_access_token(token)
        if user_id is not None:
            user = UserRepository(db).get_by_id(int(user_id))
            if user is not None:
                return user
    return get_or_create_public_user(db)

@router.post("/signup", response_model=UserResponse)
def signup(user_in: UserRegister, db: Session = Depends(get_db)):
    user_repo = UserRepository(db)
    existing = user_repo.get_by_email(user_in.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    return user_repo.create(user_in.email, user_in.password)

@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user_repo = UserRepository(db)
    user = user_repo.get_by_email(form_data.username)
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = create_access_token(subject=user.id)
    return {"access_token": access_token, "token_type": "bearer"}
