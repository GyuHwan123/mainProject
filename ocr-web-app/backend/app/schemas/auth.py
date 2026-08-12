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


class SocialLoginRequest(BaseModel):
    provider: str
    token: str
