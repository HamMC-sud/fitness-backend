from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, EmailStr
from models.enums import Region, Language


class VkSocialIn(BaseModel):
    access_token: str
    email: EmailStr
    region: Region = Region.RU
    language: Language = Language.ru
    timezone: str = "UTC"
    country: str = "RU"


class GoogleSocialIn(BaseModel):
    id_token: str
    region: Region = Region.INTL
    language: Language = Language.en
    timezone: str = "UTC"
    country: str = "INTL"


class AppleSocialIn(BaseModel):
    id_token: str
    email: Optional[EmailStr] = None
    region: Region = Region.INTL
    language: Language = Language.en
    timezone: str = "UTC"
    country: str = "INTL"
