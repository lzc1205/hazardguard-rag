"""ORM 模型统一导出。

必须在这里全部导入，否则 Alembic autogenerate 只能看到被显式引用的表，
迁移脚本会漏建表 —— 这是 Alembic 最常见的坑。
"""

from app.database import Base
from app.models.base import BaseModel
from app.models.user import User
from app.models.project import Project, Document, DocumentChunk
from app.models.review import Review, ReviewItem, ExpertPoint, Report
from app.models.knowledge import (
    KnowledgeEntry,
    KnowledgeChunk,
    KnowledgeBuildTask,
)
from app.models.chat import (
    ChatSession,
    ChatMessage,
    AsyncTask,
    PromptVersion,
)

__all__ = [
    "Base",
    "BaseModel",
    # 用户
    "User",
    # 项目与文档
    "Project",
    "Document",
    "DocumentChunk",
    # 审查域
    "Review",
    "ReviewItem",
    "ExpertPoint",
    "Report",
    # 知识库
    "KnowledgeEntry",
    "KnowledgeChunk",
    "KnowledgeBuildTask",
    # 会话与任务
    "ChatSession",
    "ChatMessage",
    "AsyncTask",
    "PromptVersion",
]