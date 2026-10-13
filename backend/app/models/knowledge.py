"""知识库域模型：knowledge_entries / knowledge_chunks / knowledge_build_tasks。

条目（entry）与分块（chunk）是两层：entry 是「一条法规条款」的业务实体，
chunk 是「送进向量库的检索单元」。一个 entry 可能被切成多个 chunk，
向量以 chunk 为主键存储，业务查询按 entry 聚合。

`is_active` + `effective_date` / `deprecated_date` 支撑时效性校验——
法规废止后必须能自动失效，否则会拿旧规范做审查，结论直接不可用。
"""

from __future__ import annotations

import uuid
from datetime import date

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import BaseModel
from app.models.enums import KnowledgeBuildType, TaskStatus


class KnowledgeEntry(BaseModel):
    """知识库条目：一条法规/规范条款，或一段问答、案例。"""

    __tablename__ = "knowledge_entries"

    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    entry_type: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True,
        comment="regulation / standard / case / template / FAQ",
    )
    category: Mapped[str | None] = mapped_column(
        String(50), index=True,
        comment="危大类别：foundation_pit/formwork/lifting/scaffold/demolition/underground/other",
    )

    # ---- 溯源三要素 ----
    regulation_name: Mapped[str | None] = mapped_column(
        String(200), index=True, comment="规范名称，如建筑施工模板安全技术规范"
    )
    regulation_code: Mapped[str | None] = mapped_column(
        String(50), index=True, comment="规范编号，如 JGJ 80-2016"
    )
    clause_number: Mapped[str | None] = mapped_column(
        String(50), comment="条款号，如 第4.2.1条"
    )
    page_number: Mapped[int | None] = mapped_column(Integer, comment="原文页码")

    # ---- 时效性 ----
    publish_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date | None] = mapped_column(Date)
    deprecated_date: Mapped[date | None] = mapped_column(Date, comment="废止日期，废止后必须置 is_active=False")
    version: Mapped[str] = mapped_column(String(20), default="1.0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # ---- 强制性条文标记（合规审查的高价值信号）----
    is_mandatory: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True, comment="是否强制性条文（必须/严禁/不得）"
    )

    # ---- 是否超过一定规模（建办质〔2018〕31号 附件2）----
    # True 的工程必须组织专家论证，风险等级与处置要求都高于普通危大工程。
    # 之所以要落成独立字段：字符 n-gram 向量分不出「超过一定规模」这层语义，
    # 两档条款在字符层面高度相似，必须靠结构化标记让检索层做确定性加权。
    is_over_scale: Mapped[bool | None] = mapped_column(
        Boolean,
        default=False,
        index=True,
        comment="是否属于超过一定规模的危大工程（附件2），True 需专家论证",
    )

    tags: Mapped[list | None] = mapped_column(JSONB)
    extra_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)

    document: Mapped["Document | None"] = relationship("Document")
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        "KnowledgeChunk", back_populates="entry", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_knowledge_entries_type_active", "entry_type", "is_active"),
        Index("ix_knowledge_entries_category_active", "category", "is_active"),
        Index("ix_knowledge_entries_code_clause", "regulation_code", "clause_number"),
    )


class KnowledgeChunk(BaseModel):
    """知识库分块——向量库的载体。

    向量以本表 id 作为 key 存在向量库中；换向量库只需迁移此表 id，
    业务数据不受影响。
    """

    __tablename__ = "knowledge_chunks"

    entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    embedding_model: Mapped[str | None] = mapped_column(
        String(100), comment="生成该向量所用的模型，换模型后需区分重建"
    )

    # ---- PGVector 存储（需求文档 4.2 节）----
    #
    # 用 vector(512) 而非 vector(1536)：当前内置字符语义 Embedding 输出 512 维，
    # 写死维度比按模型动态建表更可控——换模型时需要 ALTER COLUMN 重建，
    # 但这比每种模型一套表结构简单得多，且能提前发现维度不匹配。
    embedding: Mapped[list | None] = mapped_column(
        Vector(512),
        comment="语义向量，512维；换 Embedding 模型后需全量重建",
    )
    #: 检索用余弦距离索引；HNSW 在 512 维下召回率和速度的平衡点较好
    __table_args__ = (
        Index("ix_knowledge_chunks_entry_idx", "entry_id", "chunk_index", unique=True),
        Index(
            "ix_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    entry: Mapped["KnowledgeEntry"] = relationship(
        "KnowledgeEntry", back_populates="chunks"
    )


class KnowledgeBuildTask(BaseModel):
    """知识库构建任务（Celery 异步执行）。

    progress 字段给前端轮询用；失败时 error_message 必须落库，
    否则用户只能看到「一直卡在 99%」。
    """

    __tablename__ = "knowledge_build_tasks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_type: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="full_build / incremental_update / rebuild / delete",
    )
    status: Mapped[str] = mapped_column(
        String(20), default=TaskStatus.PENDING, nullable=False, index=True
    )

    total_chunks: Mapped[int | None] = mapped_column(Integer)
    processed_chunks: Mapped[int] = mapped_column(Integer, default=0)

    celery_task_id: Mapped[str | None] = mapped_column(String(100), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    @property
    def progress(self) -> int:
        if not self.total_chunks:
            return 100 if self.status == TaskStatus.SUCCESS else 0
        return min(int(self.processed_chunks / self.total_chunks * 100), 100)

    document: Mapped["Document"] = relationship("Document")


from app.models.project import Document  # noqa: E402  避免循环导入