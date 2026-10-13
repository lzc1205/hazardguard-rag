"""审查域模型：reviews / review_items / expert_points / reports。

这几张表是多智能体审查的产物落库位置，其中`reviews.agent_trace` 承载
全链路 Trace（JD 可观测性 11% 的落点），`review_items.regulation_reference`
承载引用溯源——两者都是本项目区别于普通 RAG Demo 的关键证据。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
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
from app.models.enums import ReviewConclusion, ReviewStatus, ReviewType


class Review(BaseModel):
    """一次合规审查任务。"""

    __tablename__ = "reviews"

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), comment="被审查的方案文档"
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=ReviewStatus.PENDING, nullable=False, index=True
    )
    review_type: Mapped[str] = mapped_column(String(20), default=ReviewType.FULL)

    # ---- HazardIdentifierAgent 的判定结果 ----
    is_hazard_project: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, comment="是否属于危大工程"
    )
    hazard_categories: Mapped[list | None] = mapped_column(
        JSONB, comment="命中的危大工程类别列表"
    )
    is_over_scale: Mapped[bool | None] = mapped_column(
        Boolean, comment="是否超过一定规模（触发专家论证门槛）"
    )
    hazard_params: Mapped[dict | None] = mapped_column(
        JSONB, comment="识别到的关键参数，如基坑深度、支撑高度"
    )

    # ---- 审查结论 ----
    overall_score: Mapped[int | None] = mapped_column(Integer, comment="综合合规评分 0-100")
    overall_conclusion: Mapped[str | None] = mapped_column(
        String(32),  # pass_with_modification 有 22 字符，VARCHAR(20) 会截断导致 500
        comment="pass / pass_with_modification / fail",
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    # ---- 全链路 Trace（JD 可观测性 11%）----
    agent_trace: Mapped[dict | None] = mapped_column(
        JSONB,
        comment="Agent 执行轨迹：每环节输入摘要/输出摘要/耗时/token/工具调用/拒答次数",
    )
    token_summary: Mapped[dict | None] = mapped_column(
        JSONB, comment="Token 消耗汇总与成本估算（JD 成本优化 10%）"
    )
    cost_estimate: Mapped[float | None] = mapped_column(
        Float, comment="本次审查成本估算（元），来自 token_summary"
    )

    project: Mapped["Project"] = relationship("Project", back_populates="reviews")
    items: Mapped[list["ReviewItem"]] = relationship(
        "ReviewItem", back_populates="review", cascade="all, delete-orphan"
    )
    expert_points: Mapped[list["ExpertPoint"]] = relationship(
        "ExpertPoint", back_populates="review", cascade="all, delete-orphan"
    )
    report: Mapped["Report | None"] = relationship(
        "Report", back_populates="review", uselist=False
    )

    __table_args__ = (
        Index("ix_reviews_project_status", "project_id", "status"),
        Index("ix_reviews_created_at", "created_at"),
    )


class ReviewItem(BaseModel):
    """单条审查意见——系统产出的最小可审查单元。

    `regulation_reference` 必须同时包含规范名称、条款号与原文摘录，
    这是「结论100% 可溯源」的存储保证；缺任一项时前端应标红为无效引用。
    """

    __tablename__ = "review_items"

    review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True, comment="审查维度，见ReviewDimension"
    )
    item_title: Mapped[str] = mapped_column(String(300), nullable=False)
    scheme_content: Mapped[str | None] = mapped_column(Text, comment="方案中对应内容摘录")

    # ---- 引用溯源（三要素缺一不可）----
    regulation_reference: Mapped[str | None] = mapped_column(
        Text, comment="引用原文，含法规名称+条款号+条款原文"
    )
    regulation_source_ids: Mapped[list | None] = mapped_column(
        JSONB, comment="关联的 knowledge_entries.id 列表"
    )
    citation_verified: Mapped[bool | None] = mapped_column(
        Boolean, default=False, comment="引用是否通过 evidence 校验（True=已验证）"
    )

    compliance_status: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True,
        comment="compliant / non_compliant / needs_confirmation / not_applicable",
    )
    severity: Mapped[str | None] = mapped_column(String(20), index=True)
    review_comment: Mapped[str] = mapped_column(Text, nullable=False)
    modification_suggestion: Mapped[str | None] = mapped_column(Text)

    agent_source: Mapped[str | None] = mapped_column(
        String(50), index=True, comment="产出该意见的 Agent 名称"
    )
    sort_order: Mapped[int | None] = mapped_column(Integer)
    tokens_used: Mapped[int | None] = mapped_column(Integer)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        comment="产生该审查意见的 Agent 或用户标识",
    )

    review: Mapped["Review"] = relationship("Review", back_populates="items")

    __table_args__ = (
        Index("ix_review_items_review_status", "review_id", "compliance_status"),
        Index("ix_review_items_review_severity", "review_id", "severity"),
    )


class ExpertPoint(BaseModel):
    """专家论证要点（超规模危大工程才生成）。"""

    __tablename__ = "expert_points"

    review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    point_title: Mapped[str] = mapped_column(String(300), nullable=False)
    point_content: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str | None] = mapped_column(String(20))
    related_regulation: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int | None] = mapped_column(Integer)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        comment="论证要点的生成来源标识",
    )

    review: Mapped["Review"] = relationship("Review", back_populates="expert_points")


class Report(BaseModel):
    """审查报告，与审查任务一一对应。"""

    __tablename__ = "reports"

    review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    report_number: Mapped[str | None] = mapped_column(String(50), unique=True)
    content_json: Mapped[dict | None] = mapped_column(JSONB, comment="报告结构化内容（各章节）")
    summary: Mapped[str | None] = mapped_column(Text, comment="报告摘要")
    word_file_path: Mapped[str | None] = mapped_column(String(500))
    pdf_file_path: Mapped[str | None] = mapped_column(String(500))
    export_status: Mapped[str] = mapped_column(String(20), default="pending")
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    review: Mapped["Review"] = relationship("Review", back_populates="report")


from app.models.project import Project  # noqa: E402  避免循环导入