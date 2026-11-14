import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal["admin", "editor", "viewer"]


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
        return value


class Registration(Credentials):
    name: str = Field(min_length=1, max_length=100)
    token: str = Field(min_length=32, max_length=256)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter your name.")
        return value.strip()


class InviteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    role: Role

    _email = field_validator("email")(Credentials.normalize_email.__func__)


class MemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role
    active: bool


class PasswordUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=12, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)
