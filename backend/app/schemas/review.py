"""审查、项目、文档、知识库、问答的 Pydantic 模型。"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- 项目


class ProjectBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    code: str | None = Field(None, max_length=50)
    description: str | None = None
    construction_unit: str | None = Field(None, max_length=200)
    supervision_unit: str | None = Field(None, max_length=200)
    design_unit: str | None = Field(None, max_length=200)
    location: str | None = Field(None, max_length=300)
    start_date: date | None = None
    end_date: date | None = None


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    # 必须覆盖 ProjectBase 的全部可改字段：漏一个，
    # update_project() 里的查重逻辑就会 AttributeError 直接 500。
    # 曾漏掉 code，导致「更新项目」接口必然崩溃。
    name: str | None = Field(None, min_length=1, max_length=200)
    code: str | None = Field(None, max_length=50)
    description: str | None = None
    status: str | None = Field(None, pattern="^(draft|in_review|reviewed|archived)$")
    construction_unit: str | None = Field(None, max_length=200)
    supervision_unit: str | None = Field(None, max_length=200)
    design_unit: str | None = Field(None, max_length=200)
    location: str | None = Field(None, max_length=300)
    start_date: date | None = None
    end_date: date | None = None


class ProjectResponse(ProjectBase):
    id: uuid.UUID
    owner_id: uuid.UUID | None
    status: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


# ---------------------------------------------------------------- 文档


class DocumentResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID | None
    title: str
    file_name: str
    file_type: str
    file_size: int | None
    doc_category: str | None
    parse_status: str
    page_count: int | None
    word_count: int | None
    created_at: datetime
    model_config = {"from_attributes": True}


class DocumentParseResponse(BaseModel):
    doc_name: str
    file_type: str
    page_count: int
    word_count: int
    is_scanned: bool
    warnings: list[str]
    full_text: str


# ---------------------------------------------------------------- 审查


class ReviewCreate(BaseModel):
    """创建审查任务。

    text 与 document_id 二选一：直接传文本用于快速演示，
    传 document_id 用于已上传文档的正式审查。
    """

    project_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    title: str = Field(..., min_length=1, max_length=300)
    text: str | None = Field(None, description="方案正文；不传则从 document_id 读取")
    review_type: str = Field("full", pattern="^(full|quick|expert_only)$")
    async_mode: bool = Field(
        False,
        description=(
            "true=投递 Celery 任务立即返回 task_id（适合大文档），"
            "false=同步执行返回完整结果（适合小方案演示）"
        ),
    )


class ReviewItemResponse(BaseModel):
    category: str
    item_title: str
    scheme_content: str | None = None
    regulation_reference: str | None = None
    compliance_status: str
    severity: str | None = None
    review_comment: str
    modification_suggestion: str | None = None
    citation_verified: bool = False
    refused: bool = False
    refuse_reason: str | None = None
    agent_source: str | None = None


class ReviewResponse(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    is_hazard_project: bool | None = None
    is_over_scale: bool | None = None
    hazard_categories: list | None = None
    overall_score: int | None = None
    overall_conclusion: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    model_config = {"from_attributes": True}


class ReviewDetailResponse(ReviewResponse):
    review_items: list[dict] = []
    expert_points: list[dict] = []
    hazard_result: dict = {}
    report: dict = {}


# ---------------------------------------------------------------- 检索


class SearchQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(8, ge=1, le=20)
    category: str | None = None
    only_mandatory: bool = False


class SearchHit(BaseModel):
    clause_no: str | None
    regulation_name: str | None
    regulation_code: str | None
    category: str | None
    page_number: int | None
    is_mandatory: bool
    content: str
    citation: str
    score: float


# ---------------------------------------------------------------- 知识库


class KnowledgeEntryCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    content: str = Field(..., min_length=1)
    entry_type: str = Field("regulation", pattern="^(regulation|standard|case|template|FAQ)$")
    category: str | None = None
    regulation_name: str | None = Field(None, max_length=200)
    regulation_code: str | None = Field(None, max_length=50)
    clause_number: str | None = Field(None, max_length=50)
    page_number: int | None = None
    is_mandatory: bool = False
    tags: list[str] = []


class KnowledgeEntryResponse(KnowledgeEntryCreate):
    id: uuid.UUID
    version: str
    is_active: bool
    created_at: datetime
    model_config = {"from_attributes": True}


# ---------------------------------------------------------------- 问答


class ChatCreate(BaseModel):
    title: str | None = Field(None, max_length=200)


class ChatSend(BaseModel):
    content: str = Field(..., min_length=1)


class ChatMessageResponse(BaseModel):
    role: str
    content: str
    retrieved_sources: list | None = None
    refused: bool = False
    refuse_reason: str | None = None
    created_at: datetime
    model_config = {"from_attributes": True}


# ---------------------------------------------------------------- 评测


class EvalRunRequest(BaseModel):
    retriever: str = Field("real", pattern="^(mock|real)$")
    tag: str = "baseline"


class EvalRunResponse(BaseModel):
    total: int
    metrics: dict
    by_category: dict
    failures: list
    duration_sec: float