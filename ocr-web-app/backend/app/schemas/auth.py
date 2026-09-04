from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


class SignupRequest(BaseModel):
    name: str = Field(max_length=80)
    email: EmailStr
    password: str = Field(max_length=200)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_email: EmailStr
    user_name: str
    user_role: str = "USER"
    user_subscription_tier: str = "FREE"
    calendar_imported: int = 0
    calendar_sync_error: str | None = None


class OAuthExchangeRequest(BaseModel):
    provider: str
    token: str
    provider_access_token: str | None = None


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(max_length=512)
    new_password: str = Field(max_length=200)


class MessageResponse(BaseModel):
    message: str
