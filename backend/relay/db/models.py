"""SQLAlchemy 2.0 ORM models for Relay.

Schema follows ``docs/DATA_MODEL.md`` PLUS the sanctioned deviations:
  * Auth + multi-tenant RLS are built (control-plane tables added below).
  * ``organization_id`` (uuid, NOT NULL) is DENORMALIZED onto EVERY tenant table -- including
    chunks, utterances, cards, card_sources, memories, leads -- so a single uniform
    ``org_isolation`` RLS policy can be applied. It defaults to the single demo org.

ID scheme (see ``relay.ids.new_id``): entity PKs are prefixed TEXT
(documents=doc, chunks=chk, sessions=ses, utterances=utt, cards=card, leads=lead,
mem, customers=cus, notifications=ntf). ``organizations.id`` and ``users.id`` are UUID
(``users.id`` == the Supabase auth ``sub``). All ``organization_id`` columns are UUID.
FKs match the referenced PK's type.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from relay.db.base import Base
from relay.ids import new_id

# Embedding dimensionality is fixed at 1024 across the project (settings.embedding_dim).
EMBEDDING_DIM = 1024


def _doc_id() -> str:
    return new_id("doc")


def _chk_id() -> str:
    return new_id("chk")


def _ses_id() -> str:
    return new_id("ses")


def _utt_id() -> str:
    return new_id("utt")


def _card_id() -> str:
    return new_id("card")


def _lead_id() -> str:
    return new_id("lead")


def _mem_id() -> str:
    return new_id("mem")


def _cus_id() -> str:
    return new_id("cus")


def _ntf_id() -> str:
    return new_id("ntf")


def _key_id() -> str:
    return new_id("key")


def _aud_id() -> str:
    return new_id("aud")


# ---------------------------------------------------------------------------
# Control plane (NO org_isolation RLS policy; self-scoped where appropriate)
# ---------------------------------------------------------------------------


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    memberships: Mapped[list["OrgMembership"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"

    # users.id == Supabase auth sub (uuid).
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="member")
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OrgMembership(Base):
    __tablename__ = "org_memberships"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    organization: Mapped["Organization"] = relationship(back_populates="memberships")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_key_id)
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    hashed_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_aud_id)
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, index=True
    )
    actor_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Tenant plane (org_isolation RLS policy applied to all of these)
# ---------------------------------------------------------------------------


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_doc_id)
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)  # pdf | docx | txt
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="processing"
    )  # processing | ready | failed
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_chk_id)
    document_id: Mapped[str] = mapped_column(
        Text, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    moss_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    document: Mapped["Document"] = relationship(back_populates="chunks")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_ses_id)
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mode: Mapped[str] = mapped_column(Text, nullable=False)  # live | desk | intake
    customer_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    livekit_room: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active"
    )  # active | ended
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    utterances: Mapped[list["Utterance"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    cards: Mapped[list["Card"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class Utterance(Base):
    __tablename__ = "utterances"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_utt_id)
    session_id: Mapped[str] = mapped_column(
        Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, index=True
    )
    speaker: Mapped[str] = mapped_column(Text, nullable=False)  # rep | prospect | customer
    text: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped["Session"] = relationship(back_populates="utterances")


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_card_id)
    session_id: Mapped[str] = mapped_column(
        Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_text: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped["Session"] = relationship(back_populates="cards")
    sources: Mapped[list["CardSource"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )


class CardSource(Base):
    __tablename__ = "card_sources"

    card_id: Mapped[str] = mapped_column(
        Text, ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    chunk_id: Mapped[str] = mapped_column(
        Text, ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, index=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    card: Mapped["Card"] = relationship(back_populates="sources")


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_cus_id)
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    company: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    memories: Mapped[list["Memory"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_mem_id)
    customer_id: Mapped[str] = mapped_column(
        Text, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # fact | summary | preference
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    source_session_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    customer: Mapped["Customer"] = relationship(back_populates="memories")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_lead_id)
    session_id: Mapped[str] = mapped_column(
        Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    company: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    qualifiers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0-100
    status: Mapped[str] = mapped_column(Text, nullable=False)  # hot | warm | cold
    routed_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Notification(Base):
    """Additive (API_SPEC account routes). Tenant-scoped; org_isolation applies."""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_ntf_id)
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "Organization",
    "User",
    "OrgMembership",
    "ApiKey",
    "AuditLog",
    "Document",
    "Chunk",
    "Session",
    "Utterance",
    "Card",
    "CardSource",
    "Customer",
    "Memory",
    "Lead",
    "Notification",
    "EMBEDDING_DIM",
]
