from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SignupRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_email: EmailStr
    user_name: str
    user_role: str = "USER"
    user_subscription_tier: str = "PERSONAL"
    calendar_imported: int = 0
    calendar_sync_error: str | None = None


class OAuthExchangeRequest(BaseModel):
    provider: str
    token: str
    provider_access_token: str | None = None
