"""回填历史文档的切块数据。

背景：上传接口早期只把全文写进 documents.parsed_content，
没有调用分块器，导致 document_chunks 表为空——审查时无法按块
定位项目文档的具体条款段落。

本脚本幂等：按 (document_id, chunk_index) 唯一索引先删后插，
可重复执行。已切块的文档会重新切分，不会产生重复数据。

用法：
    cd D:\\123456789\\PythonProject6
    .\\.venv\\Scripts\\python.exe backend\\scripts\\backfill_chunks.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import delete, select  # noqa: E402

from app.database import async_session  # noqa: E402
from app.models.project import Document, DocumentChunk  # noqa: E402
from app.rag.chunker import chunk_parsed_doc  # noqa: E402
from app.rag.parser import parse_file  # noqa: E402


async def main() -> int:
    async with async_session() as db:
        docs = (
            (await db.execute(select(Document).where(Document.is_deleted == False)))
            .scalars()
            .all()
        )
        print(f"待处理文档：{len(docs)} 份\n")

        total_chunks = 0
        ok_docs = 0
        failed: list[tuple[str, str]] = []

        for doc in docs:
            existing = (
                await db.execute(
                    select(DocumentChunk.id).where(
                        DocumentChunk.document_id == doc.id
                    ).limit(1)
                )
            ).scalar_one_or_none()

            if existing:
                print(f"  [跳过] {doc.file_name}（已有切块）")
                continue

            text = doc.parsed_content or ""
            if not text.strip():
                failed.append((doc.file_name, "无正文"))
                print(f"  [失败] {doc.file_name}：无正文")
                continue

            try:
                path = Path(doc.file_path)
                if not path.exists():
                    failed.append((doc.file_name, f"文件不存在 {path.name}"))
                    print(f"  [失败] {doc.file_name}：原文件不存在")
                    continue

                parsed = parse_file(path)
                if not parsed.pages:
                    raise ValueError("解析结果无页面")

                chunks = chunk_parsed_doc(parsed, str(doc.id), doc_name=doc.file_name)

                await db.execute(
                    delete(DocumentChunk).where(
                        DocumentChunk.document_id == doc.id
                    )
                )
                db.add_all(
                    [
                        DocumentChunk(
                            document_id=doc.id,
                            chunk_index=i,
                            content=c.content,
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
                )
                await db.commit()
                total_chunks += len(chunks)
                ok_docs += 1
                print(f"  [成功] {doc.file_name}：{len(chunks)} 块")
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                failed.append((doc.file_name, f"{type(exc).__name__}: {exc}"))
                print(f"  [失败] {doc.file_name}：{type(exc).__name__}: {exc}")

        total = (
            await db.execute(
                select(DocumentChunk.id).where(DocumentChunk.is_deleted == False)
            )
        ).all()
        print(f"\n完成：{ok_docs} 份文档，新增 {total_chunks} 个切块")
        print(f"document_chunks 当前总数：{len(total)}")
        if failed:
            print(f"\n失败 {len(failed)} 份：")
            for name, reason in failed:
                print(f"  - {name}：{reason}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
