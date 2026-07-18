"""SQLAlchemy ORM models for the persistence layer.

Mirrors the Pydantic models in `models.py`. Conversion happens at the
repository boundary (`postgres.py`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# Use JSONB on Postgres, fall back to JSON on other dialects (testing only).
JsonColumn = JSONB().with_variant(JSON(), "sqlite")


class WorkflowDefinitionRow(Base):
    __tablename__ = "workflow_definitions"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JsonColumn, nullable=False)
    org_id: Mapped[str] = mapped_column(String(64), nullable=False, server_default="default")
    owner_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class WorkflowInstanceRow(Base):
    __tablename__ = "workflow_instances"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(64), nullable=False, server_default="default")
    workflow_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trigger_payload: Mapped[dict[str, Any]] = mapped_column(JsonColumn, nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JsonColumn, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    steps: Mapped[list[StepExecutionRow]] = relationship(
        back_populates="instance", cascade="all, delete-orphan"
    )


class StepExecutionRow(Base):
    __tablename__ = "step_executions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    instance_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workflow_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    instance: Mapped[WorkflowInstanceRow] = relationship(back_populates="steps")


class AuditLogRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workflow_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    step_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JsonColumn, nullable=False)


class TriggerCursorRow(Base):
    __tablename__ = "trigger_cursors"

    trigger_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    cursor: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    seen_ids: Mapped[list[str]] = mapped_column(JsonColumn, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrganizationRow(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("iss", "sub", name="uq_users_iss_sub"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    iss: Mapped[str] = mapped_column(String(255), nullable=False)
    sub: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    org_id: Mapped[str] = mapped_column(String(64), nullable=False, server_default="default")
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    roles: Mapped[list[str]] = mapped_column(JsonColumn, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthSessionRow(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
