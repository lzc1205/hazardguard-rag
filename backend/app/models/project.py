"""项目与文档相关模型：projects / documents / document_chunks。"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import BaseModel
from app.models.enums import DocumentCategory, ParseStatus, ProjectStatus


class Project(BaseModel):
    """项目：一个工程项目，下挂多份专项方案与多次审查。"""

    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="项目名称")
    code: Mapped[str | None] = mapped_column(String(50), unique=True, comment="项目编号")
    description: Mapped[str | None] = mapped_column(Text, comment="项目描述")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), comment="负责人"
    )
    construction_unit: Mapped[str | None] = mapped_column(String(200), comment="施工单位")
    supervision_unit: Mapped[str | None] = mapped_column(String(200), comment="监理单位")
    design_unit: Mapped[str | None] = mapped_column(String(200), comment="设计单位")
    location: Mapped[str | None] = mapped_column(String(300), comment="工程地点")
    status: Mapped[str] = mapped_column(
        String(20), default=ProjectStatus.DRAFT, nullable=False, index=True
    )
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)

    owner: Mapped["User | None"] = relationship("User", lazy="selectin")
    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="project", cascade="all, delete-orphan"
    )
    reviews: Mapped[list["Review"]] = relationship(
        "Review", back_populates="project", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_projects_status_created", "status", "created_at"),
        Index("ix_projects_owner", "owner_id"),
    )


class Document(BaseModel):
    """文档：既存专项施工方案，也存法规规范（doc_category 区分）。

    方案走审查流程，法规走知识库构建流程，靠 doc_category 分流。
    """

    __tablename__ = "documents"

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False, comment="文档标题")
    file_name: Mapped[str] = mapped_column(String(300), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False, comment="pdf/docx/doc/txt")
    file_size: Mapped[int | None] = mapped_column(BigInteger, comment="字节数")
    doc_category: Mapped[str | None] = mapped_column(
        String(50), index=True, comment="special_plan/regulation/case/template/other"
    )
    construction_unit: Mapped[str | None] = mapped_column(
        String(200),
        comment="文档级的施工单位。方案审查时责任单位的主体信息，"
        "不能只挂在项目上——同一项目内不同标段施工单位不同",
    )
    supervision_unit: Mapped[str | None] = mapped_column(
        String(200), comment="文档级监理单位"
    )
    parse_status: Mapped[str] = mapped_column(
        String(20), default=ParseStatus.PENDING, nullable=False, index=True
    )
    parse_task_id: Mapped[str | None] = mapped_column(String(100))
    page_count: Mapped[int | None] = mapped_column(Integer)
    word_count: Mapped[int | None] = mapped_column(Integer)
    parsed_content: Mapped[str | None] = mapped_column(Text, comment="解析后纯文本，用于快速预览")
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    project: Mapped["Project | None"] = relationship("Project", back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        "DocumentChunk", back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_documents_category_status", "doc_category", "parse_status"),
    )


class DocumentChunk(BaseModel):
    """方案文档分块。

    只存原文与元数据，向量在向量库里，通过 chunk_id 关联——
    这样即使换向量库，业务数据也不受影响。
    """

    __tablename__ = "document_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    heading_path: Mapped[str | None] = mapped_column(String(500), comment="标题层级路径")
    page_number: Mapped[int | None] = mapped_column(Integer)
    token_count: Mapped[int | None] = mapped_column(Integer)
    extra_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, comment="扩展元数据"
    )

    document: Mapped["Document"] = relationship("Document", back_populates="chunks")

    __table_args__ = (
        Index("ix_doc_chunks_doc_idx", "document_id", "chunk_index", unique=True),
    )


from app.models.user import User  # noqa: E402  避免循环导入