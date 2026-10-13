"""项目与文档链路测试。

补 7.4.3~7.4.6 的后端能力时发现三个真实缺陷，
这里逐条锁住，防止回归。
"""

from __future__ import annotations

import pytest



def _all_paths(routes, prefix: str = "") -> list[str]:
    """递归提取全部路由路径。

    新版 FastAPI 的 `_IncludedRouter` 没有 `path` 属性，
    子路由藏在 `original_router.routes` 里。
    """
    out: list[str] = []
    for r in routes:
        p = prefix + (getattr(r, "path", "") or "")
        if type(r).__name__ == "_IncludedRouter":
            inner = getattr(r, "original_router", None)
            if inner is not None:
                out += _all_paths(
                    getattr(inner, "routes", []),
                    p + (getattr(inner, "prefix", "") or ""),
                )
        elif p:
            out.append(p)
    return out


class TestContentDisposition:
    """中文文件名必须做 RFC 5987 编码。

    真实故障：报告导出接口 500，报
    `UnicodeEncodeError: 'latin-1' codec can't encode characters`
    ——Starlette 在 `init_headers` 阶段就失败了，
    因为 HTTP 响应头只能放 latin-1 字符。
    """

    def test_chinese_filename_encoded(self):
        from app.api.v1.reviews import _content_disposition

        r = _content_disposition("深基坑开挖专项施工方案_合规审查报告.docx")
        # 必须有 RFC 5987 段
        assert "filename*=UTF-8''" in r
        # 中文必须百分号编码，不能裸放在头里
        assert "深基坑" not in r
        assert "%E6%B7%B1" in r

    def test_ascii_filename_preserved(self):
        from app.api.v1.reviews import _content_disposition

        r = _content_disposition("report.docx")
        assert "filename*=UTF-8''report.docx" in r

    def test_pure_chinese_name_has_readable_fallback(self):
        """纯中文标题的 ASCII 兜底名不能是 '_.docx' 这种怪东西。"""
        from app.api.v1.reviews import _content_disposition

        r = _content_disposition("审查报告.pdf")
        assert 'filename="compliance-review-report.pdf"' in r

    def test_extension_always_kept(self):
        from app.api.v1.reviews import _content_disposition

        for name in ("审查.docx", "审查.pdf", "方案 docx"):
            r = _content_disposition(name)
            assert Path_ext(name) in r, f"{name} 的扩展名丢失"


def Path_ext(name: str) -> str:
    """取扩展名（含点），避免在测试里重复导入。"""
    i = name.rfind(".")
    return name[i:] if i >= 0 else ""


class TestProjectSchema:
    """项目接口补齐后必须满足的条件。"""

    def test_status_labels_cover_all_enum_values(self):
        from app.api.v1.projects import STATUS_LABEL
        from app.models.enums import ProjectStatus

        for s in ProjectStatus:
            key = s.value if hasattr(s, "value") else str(s)
            assert key in STATUS_LABEL, f"项目状态 {key} 缺少中文标签"

    def test_project_routes_registered(self):
        """项目路由必须真的挂上。

        之前 schema 和模型都有，就是没写 API——
        `projects` 表一直是 0 行。

        注意：**不能直接遍历 app.routes 取 path**。
        新版 FastAPI 用 `_IncludedRouter` 包装子路由，
        顶层 routes 里只有它、`path` 属性为空，
        必须递归到 `original_router.routes` 才能拿到真实路径。
        """
        from app.main import app

        paths = _all_paths(app.routes)
        assert any(p.endswith("/projects") for p in paths), "项目列表路由未注册"
        assert any("/projects/{project_id}" in p for p in paths), "项目详情路由未注册"
        assert any("/projects/{project_id}/reviews" in p for p in paths), (
            "项目审查列表路由未注册"
        )


class TestDocumentSchema:
    """文档接口补齐后必须满足的条件。"""

    def test_document_has_unit_fields(self):
        """文档级必须有施工单位/监理单位。

        真实故障：上传接口传 construction_unit 报
        `TypeError: 'construction_unit' is an invalid keyword argument for Document`
        ——表单收了但模型没有这个字段。

        责任单位不能只挂在项目上：同一项目内不同标段施工单位不同。
        """
        from app.models.project import Document

        assert hasattr(Document, "construction_unit")
        assert hasattr(Document, "supervision_unit")

    def test_document_list_supports_pagination_and_filter(self):
        """文档列表必须支持分页与解析状态筛选。

        之前只支持 doc_category 且一次性返回全部，
        几十上百份文档时会拉爆前端。
        """
        import inspect

        from app.api.v1.documents import list_documents

        sig = inspect.signature(list_documents)
        for p in ("page", "page_size", "parse_status", "project_id"):
            assert p in sig.parameters, f"文档列表缺少 {p} 参数"

    def test_review_list_exposes_document_id(self):
        """审查列表必须返回 document_id。

        文档详情页要按它过滤出该文档的审查记录，
        缺这个字段前端只能拿全部记录在前端筛。
        """
        import inspect

        from app.api.v1.reviews import list_reviews

        src = inspect.getsource(list_reviews)
        assert '"document_id"' in src, "审查列表未返回 document_id"


class TestReviewStatusIntegrity:
    """审查记录不能出现「标记完成但没有内容」的脏数据。

    实测发现：审查中断（如 LLM 全程超时）会留下一条
    `status=completed` 但 review_items 为空的记录，
    列表页显示「完成」却是空的——极具误导性。
    """

    def test_empty_result_marked_failed(self):
        from app.services.review_persistence import persist_review_result

        src = __import__("inspect").getsource(persist_review_result)
        assert 'if status == "completed" and not review_items' in src, (
            "未对「0 条意见仍标 completed」做防护"
        )

    def test_review_list_total_respects_filters(self):
        """筛选必须同时作用于 total，否则分页数字对不上。

        真实 bug：原来 `total` 是无条件 count 全表，
        筛选「仅看超规模」后仍显示全量条数，翻页全是空。
        """
        import inspect

        from app.api.v1.reviews import list_reviews

        src = inspect.getsource(list_reviews)
        # 用了带条件的 count
        assert "select(func.count())" in src, "total 未使用 count 查询"
        # 不能出现无条件 count 全表
        assert "select(Review.id).where(Review.is_deleted == False)).scalars()" not in src, (
            "total 仍是无条件全表计数"
        )

    def test_review_list_supports_all_filters(self):
        import inspect

        from app.api.v1.reviews import list_reviews

        sig = inspect.signature(list_reviews)
        for p in (
            "project_id",
            "document_id",
            "overall_conclusion",
            "is_over_scale",
            "keyword",
        ):
            assert p in sig.parameters, f"审查列表缺少 {p} 筛选参数"


class TestErrorSurfacing:
    """错误信息必须能传到用户面前。

    真实 bug：拦截器只读 `data.message`，
    但 FastAPI 的 HTTPException 用 `detail` 字段，
    导致上传失败时前端只弹「Network error」，
    用户看不到「不支持的文件类型 .doc」这种可操作的原因。
    """

    def test_upload_error_messages_are_actionable(self):
        """上传失败提示必须告诉用户怎么解决。"""
        import inspect

        from app.api.v1.documents import upload_document

        src = inspect.getsource(upload_document)
        assert "另存为 .docx" in src, "缺少 Word 转换指引"
        assert "不支持的文件类型" in src

    def test_parse_failure_is_captured(self):
        """解析异常必须捕获，不能抛 500。

        损坏的 docx / 加密 PDF 会让底层解析库抛异常，
        用户看到「服务器错误」完全不知道怎么办。
        """
        import inspect

        from app.api.v1.documents import upload_document

        src = inspect.getsource(upload_document)
        assert "except Exception" in src, "解析过程未捕获异常"
        assert "parse_error" in src, "未记录解析失败原因"

    def test_frontend_interceptor_reads_detail_field(self):
        """前端拦截器必须读 detail 字段。"""
        from pathlib import Path

        # 本文件在 backend/tests/test_services/，前端在 ../../frontend
        root = Path(__file__).resolve().parents[3]
        p = root / "frontend" / "src" / "api" / "request.ts"
        assert p.exists(), f"未找到前端请求封装：{p}"
        src = p.read_text(encoding="utf-8")
        assert "body?.detail" in src, "拦截器未读取 detail 字段（HTTPException 用它）"
        assert "Network Error" in src, "未对网络不可达给出可读提示"
