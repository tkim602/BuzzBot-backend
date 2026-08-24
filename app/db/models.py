"""Database models for BuzzBot — Postgres + pgvector."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_policy_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    documents: Mapped[list[Document]] = relationship(back_populates="source")


class Document(Base):
    __tablename__ = "documents"

    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False
    )
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    etag: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(256), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    source: Mapped[Source] = relationship(back_populates="documents")
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base):
    __tablename__ = "chunks"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.doc_id"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    headings: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    document: Mapped[Document] = relationship(back_populates="chunks")
    embedding: Mapped[Embedding | None] = relationship(
        back_populates="chunk", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_chunks_doc_id", "doc_id"),
        Index("ix_chunks_source_id", "source_id"),
    )


class Embedding(Base):
    __tablename__ = "embeddings"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.chunk_id"), primary_key=True
    )
    embedding = mapped_column(Vector(1536), nullable=False)

    chunk: Mapped[Chunk] = relationship(back_populates="embedding")


class FetchState(Base):
    __tablename__ = "fetch_state"

    url: Mapped[str] = mapped_column(String(2048), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    etag: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(256), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PLANNED")
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('PLANNED', 'RUNNING', 'PAUSED', 'COMPLETED', 'PARTIAL', 'FAILED')",
            name="ck_ingestion_runs_status",
        ),
        CheckConstraint("concurrency > 0", name="ck_ingestion_runs_concurrency"),
        CheckConstraint("retry_limit >= 0", name="ck_ingestion_runs_retry_limit"),
    )


class IngestionRunUnit(Base):
    __tablename__ = "ingestion_run_units"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False
    )
    unit_key: Mapped[str] = mapped_column(String(128), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_ingestion_run_units_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_ingestion_run_units_attempts"),
        CheckConstraint("position >= 0", name="ck_ingestion_run_units_position"),
        UniqueConstraint("run_id", "unit_key", name="uq_ingestion_run_units_key"),
        UniqueConstraint("run_id", "position", name="uq_ingestion_run_units_position"),
    )


class DataVersion(Base):
    __tablename__ = "data_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_unit: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="STAGED")
    row_counts_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    academic_terms: Mapped[list[AcademicTerm]] = relationship(
        back_populates="data_version", cascade="all, delete-orphan"
    )
    courses: Mapped[list[Course]] = relationship(
        back_populates="data_version", cascade="all, delete-orphan"
    )
    sections: Mapped[list[Section]] = relationship(
        back_populates="data_version", cascade="all, delete-orphan"
    )
    meetings: Mapped[list[Meeting]] = relationship(
        back_populates="data_version", cascade="all, delete-orphan"
    )
    source_snapshots: Mapped[list[SourceSnapshot]] = relationship(
        back_populates="data_version", cascade="all, delete-orphan"
    )
    ingestion_errors: Mapped[list[IngestionError]] = relationship(
        back_populates="data_version", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('STAGED', 'PUBLISHED', 'FAILED', 'SUPERSEDED')",
            name="ck_data_versions_status",
        ),
        Index(
            "ix_data_versions_published_lookup",
            "provider",
            "requested_unit",
            "status",
            "published_at",
        ),
    )


class AcademicTerm(Base):
    __tablename__ = "academic_terms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    data_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_versions.id", ondelete="CASCADE"), nullable=False
    )
    term_code: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)

    data_version: Mapped[DataVersion] = relationship(back_populates="academic_terms")
    sections: Mapped[list[Section]] = relationship(back_populates="academic_term", viewonly=True)

    __table_args__ = (
        UniqueConstraint("data_version_id", "term_code", name="uq_academic_terms_version_code"),
        UniqueConstraint("data_version_id", "id", name="uq_academic_terms_version_id"),
    )


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    data_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_versions.id", ondelete="CASCADE"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(16), nullable=False)
    course_number: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    credits: Mapped[float] = mapped_column(Float, nullable=False)
    prerequisites_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    data_version: Mapped[DataVersion] = relationship(back_populates="courses")
    sections: Mapped[list[Section]] = relationship(back_populates="course", viewonly=True)

    __table_args__ = (
        UniqueConstraint(
            "data_version_id",
            "subject",
            "course_number",
            name="uq_courses_version_subject_number",
        ),
        UniqueConstraint("data_version_id", "id", name="uq_courses_version_id"),
    )


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    data_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_versions.id", ondelete="CASCADE"), nullable=False
    )
    academic_term_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    course_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    term_code: Mapped[str] = mapped_column(String(16), nullable=False)
    crn: Mapped[str] = mapped_column(String(16), nullable=False)
    section_code: Mapped[str] = mapped_column(String(16), nullable=False)
    campus: Mapped[str] = mapped_column(String(128), nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(128), nullable=False)
    instructional_method: Mapped[str | None] = mapped_column(String(128), nullable=True)
    instructors_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    data_version: Mapped[DataVersion] = relationship(back_populates="sections")
    academic_term: Mapped[AcademicTerm] = relationship(back_populates="sections", viewonly=True)
    course: Mapped[Course] = relationship(back_populates="sections", viewonly=True)
    meetings: Mapped[list[Meeting]] = relationship(back_populates="section", viewonly=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["data_version_id", "academic_term_id"],
            ["academic_terms.data_version_id", "academic_terms.id"],
            name="fk_sections_version_term",
        ),
        ForeignKeyConstraint(
            ["data_version_id", "course_id"],
            ["courses.data_version_id", "courses.id"],
            name="fk_sections_version_course",
        ),
        UniqueConstraint(
            "data_version_id", "term_code", "crn", name="uq_sections_version_term_crn"
        ),
        UniqueConstraint("data_version_id", "id", name="uq_sections_version_id"),
        Index(
            "ix_sections_instructors_json",
            "instructors_json",
            postgresql_using="gin",
        ),
    )


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    data_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_versions.id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    meeting_type: Mapped[str] = mapped_column(String(64), nullable=False)
    days: Mapped[str | None] = mapped_column(String(16), nullable=True)
    start_time: Mapped[time | None] = mapped_column(Time(), nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time(), nullable=True)
    start_date: Mapped[date] = mapped_column(Date(), nullable=False)
    end_date: Mapped[date] = mapped_column(Date(), nullable=False)
    building: Mapped[str | None] = mapped_column(String(256), nullable=True)
    room: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_tba: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    data_version: Mapped[DataVersion] = relationship(back_populates="meetings")
    section: Mapped[Section] = relationship(back_populates="meetings", viewonly=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["data_version_id", "section_id"],
            ["sections.data_version_id", "sections.id"],
            name="fk_meetings_version_section",
        ),
        Index("ix_meetings_days_times", "days", "start_time", "end_time"),
    )


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    data_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_versions.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(256), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_location: Mapped[str] = mapped_column(String(2048), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)

    data_version: Mapped[DataVersion] = relationship(back_populates="source_snapshots")


class IngestionError(Base):
    __tablename__ = "ingestion_errors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    data_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_versions.id", ondelete="CASCADE"), nullable=False
    )
    error_code: Mapped[str] = mapped_column(String(64), nullable=False)
    record_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    data_version: Mapped[DataVersion] = relationship(back_populates="ingestion_errors")
