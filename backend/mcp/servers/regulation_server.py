"""MCP 工具服务器。

对应 JD 频次 20%（校招 17% / 社招 29%，涨势最强）。

设计要点：
1. **docstring 就是给模型看的工具说明**——必须写清「何时该用」，
   模型靠它判断调用时机与传参，写不好工具就等于不存在。
2. 每个工具用 Pydantic 定义 schema——模型依赖 schema 做参数校验。
3. 工具内部复用 services 层，不重复实现检索逻辑。
4. 提供独立自测脚本，可脱离 LLM 验证行为。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services import retrieval_service as svc

try:
    from mcp.server.fastmcp import FastMCP
    HAS_MCP = True
except ImportError:  # pragma: no cover
    FastMCP = None
    HAS_MCP = False

mcp = FastMCP("hazardguard-regulation-tools") if HAS_MCP else None


# ---------------------------------------------------------------- 入参 Schema


class RegulationQuery(BaseModel):
    """规范检索入参。"""

    keyword: str | None = Field(
        None, description="检索关键词，如'立杆间距''超规模''专家论证'。口语化描述也可以。"
    )
    clause_no: str | None = Field(
        None, description="精确条款号，如'第4.2.1条'。用户明确点名条款时必填，命中精度最高。"
    )
    category: str | None = Field(
        None,
        description=(
            "危大工程类别，可选值：foundation_pit(基坑)/formwork(模板)/lifting(起重吊装)"
            "/scaffold(脚手架)/demolition(拆除爆破)/underground(暗挖)/other(其他)"
        ),
    )
    top_k: int = Field(5, description="返回条数，范围 1-20，默认 5。审查类问题建议 5-8。")
    only_mandatory: bool = Field(
        False, description="是否只返回强制性条文。判断'是否违规'时应置 True。"
    )


class ClauseDetailQuery(BaseModel):
    """条款原文查询入参。"""

    regulation_code: str = Field(..., description="规范编号，如 'JGJ 80-2016'")
    clause_no: str = Field(..., description="条款号，如 '第4.2.1条'")


class ValidityQuery(BaseModel):
    """规范时效性校验入参。"""

    regulation_code: str = Field(..., description="要校验的规范编号，如 'JGJ 80-2016'")


class DocumentParseQuery(BaseModel):
    """文档解析入参。"""

    file_path: str = Field(..., description="待解析文件的绝对路径，支持 PDF/DOCX/TXT")
    include_pages: bool = Field(
        False, description="是否返回分页明细。文档较长时建议 True，便于定位条款页码。"
    )


# ---------------------------------------------------------------- 工具实现


def _search_regulation(query: RegulationQuery) -> list[dict]:
    # 只给 clause_no 时，必须以条款号作为检索词——
    # 否则关键词为空，向量与 BM25 都无从匹配。
    keyword = query.keyword or query.clause_no or ""
    results = svc.search(
        query=keyword,
        top_k=query.top_k,
        category=query.category,
        clause_no=query.clause_no,
    )
    if query.only_mandatory:
        results = [r for r in results if r["is_mandatory"]]
    return results


def _get_clause_detail(query: ClauseDetailQuery) -> dict:
    return svc.get_clause_detail(query.regulation_code, query.clause_no) or {
        "error": f"未找到 {query.regulation_code} {query.clause_no}"
    }


def _check_version_validity(query: ValidityQuery) -> dict:
    return svc.check_version_validity(query.regulation_code)


def _parse_document(query: DocumentParseQuery) -> dict:
    from app.rag.parser import parse_file

    try:
        parsed = parse_file(query.file_path)
    except FileNotFoundError as e:
        return {"error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"error": f"解析失败: {type(e).__name__}: {e}"}

    result: dict[str, Any] = {
        "doc_name": parsed.doc_name,
        "file_type": parsed.file_type,
        "page_count": len(parsed.pages),
        "word_count": len(parsed.full_text),
        "is_scanned": parsed.is_scanned,
        "warnings": parsed.warnings,
        "full_text": parsed.full_text,
    }
    if query.include_pages:
        result["pages"] = [
            {"page_no": p.page_no, "char_count": len(p.text)} for p in parsed.pages
        ]
    return result


def _list_hazard_categories() -> list[dict]:
    return svc.list_hazard_categories()


def _export_report(payload: dict) -> dict:
    """导出合规审查报告为 Word 文件。

    payload 结构与 /api/v1/reviews/{id}/report.preview 返回一致，
    因此 Agent 链与 HTTP 接口共用同一套渲染逻辑。
    """
    from app.services.report_service import generate_word_report
    from app.config import settings
    from pathlib import Path

    try:
        out_dir = Path(settings.UPLOAD_DIR).parent / "exports"
        filename = f"{payload.get('report_number') or 'review'}_report.docx"
        path = generate_word_report(payload, out_dir / filename)
        return {
            "status": "success",
            "path": str(path),
            "file_name": filename,
            "size_bytes": path.stat().st_size,
            "format": "docx",
        }
    except Exception as e:  # noqa: BLE001
        # 导出失败不能中断审查流程——报告只是交付物，结论数据仍在库里
        return {
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "message": "报告导出失败，审查结论数据仍可通过 /reviews/{id} 获取",
        }


# ---------------------------------------------------------------- 工具注册

if HAS_MCP:

    @mcp.tool()
    async def search_regulation(query: RegulationQuery) -> list[dict]:
        """检索现行有效的工程法规与规范条文，返回带条款号和规范编号的结果。

        审查危大工程专项方案时，用于查找合规依据。返回的每条结果都包含
        规范名称、条款号、条款原文，可直接引用作为审查意见的依据。

        典型用法：
        - 判断某个技术参数是否合规 → 传关键词如「立杆间距」
        - 用户明确问「JGJ 80 第4.2.1条说什么」 → 传 clause_no
        - 需要判定是否违规 → 设置 only_mandatory=True只看强制性条文
        """
        return _search_regulation(query)

    @mcp.tool()
    async def get_clause_detail(query: ClauseDetailQuery) -> dict:
        """按规范编号和条款号获取条款完整原文。

        当检索结果只返回了片段、需要查看条文全文时使用。
        返回内容含条款原文与页码，可直接作为引用溯源。
        """
        return _get_clause_detail(query)

    @mcp.tool()
    async def check_version_validity(query: ValidityQuery) -> dict:
        """校验规范是否现行有效（是否已废止）。

        审查前必须调用。法规废止后仍引用会导致审查结论无效，
        这是合规审查系统的红线。返回 is_active=False 时不得将该规范作为依据。
        """
        return _check_version_validity(query)

    @mcp.tool()
    async def parse_document(query: DocumentParseQuery) -> dict:
        """解析上传的专项施工方案文档，返回结构化纯文本。

        审查流程的第一步。支持 PDF / DOCX / TXT，能处理双栏排版与表格。
        扫描件（无文本层）会在 warnings 中提示，需要 OCR 处理。
        """
        return _parse_document(query)

    @mcp.tool()
    async def list_hazard_categories() -> list[dict]:
        """返回危大工程七大类别的判定阈值。

        识别阶段使用。返回每一类的危大阈值与超规模阈值，
        用于判断方案是否属于危大工程、是否需要专家论证。
        """
        return _list_hazard_categories()

    @mcp.tool()
    async def export_report(payload: dict) -> dict:
        """导出合规审查报告。

        审查流程的最后一步。接收审查意见、论证要点等结构化数据，
        生成 Word 或 PDF 报告。
        """
        return _export_report(payload)


def main() -> None:
    """以 stdio 方式启动 MCP 服务器，供 LangChain MCP 适配器连接。"""
    if not HAS_MCP:
        print("未安装 mcp SDK，请执行 pip install mcp", file=sys.stderr)
        raise SystemExit(1)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()