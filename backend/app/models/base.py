import uuid
from datetime import datetime
from sqlalchemy import func, String, Boolean, DateTime, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class BaseModel(Base):
    __abstract__ = True

    # 必须用 sqlalchemy.Uuid（原生支持 PG 的 uuid 类型），
    # 标准库 uuid.UUID 没有 as_uuid 参数，直接传会抛 TypeError 导致应用起不来。
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
