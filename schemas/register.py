from pydantic import BaseModel
from models.users import UserProfile



class LoginIn(BaseModel):
    identifier: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str


class ForgotPasswordIn(BaseModel):
    email: str


class ResetPasswordIn(BaseModel):
    email: str
    new_password: str


class RegisterStartIn(BaseModel):
    email: str
    password: str


class RegisterVerifyIn(BaseModel):
    email: str 
    code: str


class ResendCodeIn(BaseModel):
    email: str


class RegisterCompleteIn(BaseModel):
    email: str
    profile: UserProfile
