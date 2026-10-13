"""会话与任务模型：chat_sessions / chat_messages / async_tasks。

chat_messages.retrieved_sources 存检索到的法规来源（仅 assistant 消息），
是问答类中「答案可信度」的唯一依据——前端必须能把来源展示出来，
否则用户无法判断该不该信这个答案。

async_tasks 是 Celery 任务的业务侧镜像：Celery 自己的状态查起来不方便，
前端轮询这个表即可。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import BaseModel
from app.models.enums import TaskStatus


class ChatSession(BaseModel):
    """智能问答会话。"""

    __tablename__ = "chat_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="新会话")

    # 长上下文会话的策略：由上下文工程模块（第 10 章）读取
    context_strategy: Mapped[dict | None] = mapped_column(
        JSONB,
        comment="上下文策略：token预算、摘要轮数阈值、压缩开关（JD 上下文工程 21%）",
    )
    token_budget: Mapped[int | None] = mapped_column(
        Integer, comment="本会话 token 预算上限"
    )
    tokens_used: Mapped[int | None] = mapped_column(Integer, default=0)

    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="session", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_chat_sessions_user_updated", "user_id", "updated_at"),)


class ChatMessage(BaseModel):
    """单条对话消息。"""

    __tablename__ = "chat_messages"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, comment="user / assistant")
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # ---- 溯源（仅 assistant 消息）----
    retrieved_sources: Mapped[list | None] = mapped_column(
        JSONB, comment="检索到的法规来源，含规范名/条款号/原文/相似度"
    )
    refused: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否因证据不足被拒答"
    )
    refuse_reason: Mapped[str | None] = mapped_column(Text)

    tokens_used: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[float | None] = mapped_column(Float, comment="本次调用成本估算")

    session: Mapped["ChatSession"] = relationship("ChatSession", back_populates="messages")

    __table_args__ = (Index("ix_chat_messages_session_created", "session_id", "created_at"),)


class AsyncTask(BaseModel):
    """异步任务镜像表。"""

    __tablename__ = "async_tasks"

    celery_task_id: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    task_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True,
        comment="document_parse / knowledge_build / report_export / review",
    )
    status: Mapped[str] = mapped_column(
        String(20), default=TaskStatus.PENDING, nullable=False, index=True
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, comment="进度 0-100")
    current_stage: Mapped[str | None] = mapped_column(
        String(100),
        comment="当前执行环节的中文描述，供前端显示「正在做合规审查…」",
    )

    # 关联的业务对象，前端据此跳转
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE")
    )
    review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE")
    )

    params: Mapped[dict | None] = mapped_column(JSONB, comment="任务入参")
    result: Mapped[dict | None] = mapped_column(JSONB, comment="结果数据")
    error_message: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class PromptVersion(BaseModel):
    """Prompt 版本记录（JD 可观测性 11%：Prompt 版本管理）。

    每次改动 Prompt 都要新建版本并跑评测，`eval_score` 记录评测结果——
    这样才能证明「迭代有效」，而不是凭感觉改。
    """

    __tablename__ = "prompt_versions"

    name: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True, comment="Prompt 名称，如 compliance_reviewer"
    )
    version: Mapped[str] = mapped_column(String(20), nullable=False)

    template: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list | None] = mapped_column(JSONB, comment="模板变量列表")

    changelog: Mapped[str | None] = mapped_column(Text, comment="改动说明")
    eval_score: Mapped[dict | None] = mapped_column(
        JSONB, comment="该版本的评测指标，如 {recall_at_5: 0.96, faithfulness: 0.94}"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    __table_args__ = (
        Index("ix_prompt_versions_name_version", "name", "version", unique=True),
    )


from app.models.project import Document, Project  # noqa: E402  避免循环导入
from app.models.review import Review  # noqa: E402  避免循环导入