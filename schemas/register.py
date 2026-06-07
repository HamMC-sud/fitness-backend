from typing import Optional

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
    code: str
    new_password: str
    confirm_new_password: Optional[str] = None


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str
    confirm_new_password: Optional[str] = None


class RegisterStartIn(BaseModel):
    email: str
    password: str


class RegisterVerifyIn(BaseModel):
    email: str 
    code: str


class RegisterCompleteIn(BaseModel):
    email: str
    profile: UserProfile
