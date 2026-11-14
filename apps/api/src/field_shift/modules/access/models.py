from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from field_shift.db import Base


class User(Base):
    __tablename__ = "access_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Membership(Base):
    __tablename__ = "access_memberships"
    __table_args__ = (CheckConstraint("role IN ('admin', 'editor', 'viewer')", name="role"),)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("access_users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(12))
    active: Mapped[bool]


class LoginSession(Base):
    __tablename__ = "access_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("access_users.id"), index=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Invitation(Base):
    __tablename__ = "access_invitations"
    __table_args__ = (CheckConstraint("role IN ('admin', 'editor', 'viewer')", name="role"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    email: Mapped[str] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(String(12))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccessThrottle(Base):
    __tablename__ = "access_throttles"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int]
