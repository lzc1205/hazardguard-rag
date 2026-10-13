"""文档上传与解析 API。

对应需求文档 4.3 节。文件上传是审查流程的入口。
安全要求（文档 13.2 节）：MIME + 扩展名 + 文件头三重校验。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.config import settings
from app.database import get_db
from app.models.project import Document
from app.models.user import User

router = APIRouter(prefix="/documents", tags=["documents"])

# 允许的文件类型与对应魔数（文件头）
ALLOWED = {
    ".pdf": [b"%PDF-"],
    ".docx": [b"PK\x03\x04"],  # docx 是 zip
    ".txt": None,  # 纯文本无固定魔数，靠扩展名与可解码性判断
}

MAX_SIZE = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(...),
    project_id: str | None = Form(None),
    doc_category: str = Form("special_plan"),
    construction_unit: str | None = Form(None),
    supervision_unit: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """上传方案文档并解析。

    三重校验：扩展名 → MIME 类型 → 文件头魔数，防止伪装扩展名的攻击。

    **解析失败必须兜住**，不能抛 500：
    损坏的 docx、加密 PDF、扫描件都会让底层解析库抛异常，
    用户看到「服务器错误」完全不知道该怎么办。
    这里统一捕获，文档照常入库并标记 failed + 具体原因，
    前端能看到「解析失败：加密 PDF」而不是白屏。
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(
            status_code=400,
            detail=(
                f"不支持的文件类型 {suffix}。"
                f"请上传 {', '.join(ALLOWED)} 格式"
                f"（Word 请另存为 .docx，旧版 .doc 与 WPS 格式请先转换）"
            ),
        )

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(
            status_code=413, detail=f"文件超过 {settings.MAX_UPLOAD_SIZE_MB}MB 上限"
        )
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")

    # 文件头校验
    expected = ALLOWED[suffix]
    if expected and not any(content.startswith(magic) for magic in expected):
        raise HTTPException(
            status_code=400,
            detail=(
                f"文件头校验失败：这不是真实的 {suffix} 文件。"
                "如果是用 WPS/Office 另存的文件，请在软件里"
                "另存为标准格式后再上传"
            ),
        )

    # 保存
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    file_path = upload_dir / stored_name
    file_path.write_bytes(content)

    # 解析：任何异常都要兜住并给出人话原因
    from app.rag.parser import parse_file

    parse_warnings: list[str] = []
    parse_error: str | None = None
    is_scanned = False
    try:
        parsed = parse_file(file_path)
        full_text = parsed.full_text
        page_count = len(parsed.pages)
        parse_warnings = list(parsed.warnings or [])
        is_scanned = bool(getattr(parsed, "is_scanned", False))
        if parsed.is_scanned and not full_text:
            parse_error = "扫描件无文本层，需先 OCR 才能审查"
        elif not full_text:
            parse_error = "未解析出正文，可能是空文件或纯图片"
    except Exception as exc:  # noqa: BLE001
        full_text = ""
        page_count = 0
        parse_error = f"解析失败：{type(exc).__name__}: {exc}"[:500]

    doc = Document(
        project_id=uuid.UUID(project_id) if project_id else None,
        title=title,
        file_name=file.filename or stored_name,
        file_path=str(file_path),
        file_type=suffix.lstrip("."),
        file_size=len(content),
        doc_category=doc_category,
        construction_unit=construction_unit,
        supervision_unit=supervision_unit,
        parse_status="parsed" if full_text else "failed",
        page_count=page_count,
        word_count=len(full_text),
        parsed_content=full_text[:200_000],  # 截断防止单行超长
        uploaded_by=current_user.id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    # 切块入库：审查时要按块检索项目文档（比对方案做法 vs 法规条款），
    # 只存全文无法定位到具体条款段落。这里失败不回滚上传，
    # 但要把原因带回前端——否则用户看到的仍是「解析成功」。
    chunk_count = 0
    chunk_error: str | None = None
    if full_text:
        try:
            chunk_count = await _persist_document_chunks(db, doc, parsed)
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            chunk_error = f"切块入库失败：{type(exc).__name__}: {exc}"[:300]

    # 解析失败时 message 带原因，前端据此提示
    message = "success" if full_text else (parse_error or "解析失败")
    if parse_warnings:
        message = f"{message}（{'；'.join(parse_warnings[:2])}）"
    if chunk_error:
        message = f"{message}；{chunk_error}"

    return {
        "code": 0,
        "message": message,
        "data": {
            "id": str(doc.id),
            "title": doc.title,
            "file_name": doc.file_name,
            "parse_status": doc.parse_status,
            "page_count": doc.page_count,
            "word_count": doc.word_count,
            "chunk_count": chunk_count,
            "warnings": parse_warnings,
            "parse_error": parse_error,
            "chunk_error": chunk_error,
            "is_scanned": is_scanned,
        },
    }


async def _persist_document_chunks(db, doc: Document, parsed) -> int:
    """把解析结果切块并写入 document_chunks（幂等，可重复调用）。"""
    from app.models.project import DocumentChunk
    from app.rag.chunker import chunk_parsed_doc

    # 重复解析同一文档时先清旧块，避免 chunk_index 唯一索引冲突
    await db.execute(
        delete(DocumentChunk).where(DocumentChunk.document_id == doc.id)
    )

    chunks = chunk_parsed_doc(parsed, str(doc.id), doc_name=doc.file_name)
    if not chunks:
        return 0

    rows = [
        DocumentChunk(
            document_id=doc.id,
            chunk_index=i,
            content=c.content,
            # 溯源信息塞进 metadata：TextChunk 用 clause_no/section 表达，
            # DocumentChunk 是固定列结构，映射关系集中在这里
            page_number=c.page,
            token_count=len(c.content),
            extra_metadata={
                "clause_no": c.clause_no,
                "section": c.section,
                "is_mandatory": c.is_mandatory,
            },
        )
        for i, c in enumerate(chunks)
    ]
    db.add_all(rows)
    await db.commit()
    return len(rows)


@router.get("")
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    project_id: str | None = None,
    doc_category: str | None = None,
    parse_status: str | None = None,
    keyword: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """文档列表（支持分页与筛选）。

    前端文档管理页要按项目/解析状态筛选，还要分页，
    所以这里必须补齐——原先只支持 doc_category 且不分页，
    真实使用（几十上百份文档）时会一次拉全量。
    """
    from sqlalchemy import func

    cond = [Document.is_deleted == False]  # noqa: E712
    if project_id:
        cond.append(Document.project_id == uuid.UUID(project_id))
    if doc_category:
        cond.append(Document.doc_category == doc_category)
    if parse_status:
        cond.append(Document.parse_status == parse_status)
    if keyword:
        like = f"%{keyword}%"
        cond.append(
            (Document.title.ilike(like)) | (Document.file_name.ilike(like))
        )

    total = (
        await db.execute(select(func.count()).select_from(Document).where(*cond))
    ).scalar_one()

    rows = (
        (
            await db.execute(
                select(Document)
                .where(*cond)
                .order_by(Document.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    items = [
        {
            "id": str(d.id),
            "project_id": str(d.project_id) if d.project_id else None,
            "title": d.title,
            "file_name": d.file_name,
            "file_type": d.file_type,
            "file_size": d.file_size,
            "doc_category": d.doc_category,
            "construction_unit": d.construction_unit,
            "supervision_unit": d.supervision_unit,
            "parse_status": d.parse_status,
            "page_count": d.page_count,
            "word_count": d.word_count,
            "parsed_content_length": len(d.parsed_content) if d.parsed_content else 0,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in rows
    ]
    return {
        "code": 0,
        "message": "success",
        "data": {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    }


@router.get("/{document_id}")
async def get_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """文档详情，含解析后的正文预览。"""
    doc = await db.get(Document, uuid.UUID(document_id))
    if not doc or doc.is_deleted:
        return {"code": 40400, "message": "文档不存在", "data": None}
    return {
        "code": 0,
        "message": "success",
        "data": {
            "id": str(doc.id),
            "project_id": str(doc.project_id) if doc.project_id else None,
            "title": doc.title,
            "file_name": doc.file_name,
            "file_type": doc.file_type,
            "file_size": doc.file_size,
            "doc_category": doc.doc_category,
            "construction_unit": doc.construction_unit,
            "supervision_unit": doc.supervision_unit,
            "parse_status": doc.parse_status,
            "page_count": doc.page_count,
            "word_count": doc.word_count,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "content_length": len(doc.parsed_content) if doc.parsed_content else 0,
            "content_preview": (doc.parsed_content or "")[:20000],
        },
    }


@router.get("/{document_id}/download")
async def download_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """下载原文件（经鉴权，不走静态目录直链）。"""
    from fastapi.responses import FileResponse

    doc = await db.get(Document, uuid.UUID(document_id))
    if not doc or doc.is_deleted:
        raise HTTPException(status_code=404, detail="文档不存在")

    path = Path(doc.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件已丢失")

    # 强制下载，避免 XSS（txt/html 类文件尤其重要）
    return FileResponse(
        path, filename=doc.file_name, media_type="application/octet-stream"
    )


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除文档（软删除记录，保留文件便于追溯）。"""
    doc = await db.get(Document, uuid.UUID(document_id))
    if not doc or doc.is_deleted:
        return {"code": 40400, "message": "文档不存在", "data": None}
    doc.is_deleted = True
    await db.commit()
    return {"code": 0, "message": "success", "data": None}