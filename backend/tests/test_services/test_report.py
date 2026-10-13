"""报告导出测试（P2 交付项）。

报告是审查结果的交付出口，必须验证：
1. Word 文件能生成且可被解析
2. 每个结论都带法规依据（无依据要显式标注）
3. 拒答项在报告中被明确告知——不能让使用者误以为「全部已审查」
"""

from __future__ import annotations

import io
import re

import pytest

from app.services.report_service import (
    ReportGenerator,
    generate_docx_bytes,
    generate_word_report,
)

SAMPLE_REVIEW = {
    "title": "XX 项目深基坑开挖专项方案合规审查报告",
    "project_name": "XX 建设项目 A 标段",
    "is_hazard_project": True,
    "is_over_scale": True,
    "hazard_categories": ["foundation_pit"],
    "overall_score": 77,
    "overall_conclusion": "pass_with_modification",
    "review_items": [
        {
            "category": "completeness",
            "item_title": "专项方案必备内容完整性",
            "review_comment": "已包含住建部令第十一条规定的全部 9 项必备内容。",
            "regulation_reference": "《危险性较大的分部分项工程安全管理条例》第11条",
            "citation_verified": True,
            "compliance_status": "compliant",
            "severity": "suggestion",
            "modification_suggestion": None,
            "agent_source": "ComplianceReviewerAgent",
        },
        {
            "category": "monitoring",
            "item_title": "监测方案审查",
            "review_comment": "方案中未检索到监测相关明确内容。",
            "regulation_reference": None,
            "citation_verified": False,
            "compliance_status": "needs_confirmation",
            "severity": "minor",
            "modification_suggestion": "建议补充监测方案章节",
            "agent_source": "ComplianceReviewerAgent",
            "refused": True,
            "refuse_reason": "知识库中未找到该维度的直接法规依据，按证据闸门拒绝出具判定",
        },
        {
            "category": "procedure",
            "item_title": "审批程序合规性",
            "review_comment": "未找到技术负责人签字与总监理工程师审查记录。",
            "regulation_reference": None,
            "citation_verified": False,
            "compliance_status": "non_compliant",
            "severity": "critical",
            "modification_suggestion": "补充审批签字页",
            "agent_source": "ComplianceReviewerAgent",
            "refused": True,
            "refuse_reason": "方案无相关内容",
        },
    ],
    "expert_points": [
        {
            "point_title": "受力计算与设计验算论证",
            "point_content": "需提供计算书并论证计算模型、荷载取值的正确性。",
            "risk_level": "high",
            "related_regulation": "《条例》第13条",
        }
    ],
    "agent_trace": [
        {
            "agent": "DocumentParserAgent",
            "duration_ms": 1,
            "tokens": 307,
            "output_summary": "抽取到基坑开挖深度 6.5m",
        },
        {
            "agent": "ComplianceReviewerAgent",
            "duration_ms": 0,
            "tokens": 2057,
            "output_summary": "出具 9 条意见",
        },
    ],
    "total_tokens": 7781,
    "cost_estimate": 0.0156,
}


@pytest.fixture(scope="module")
def docx_bytes() -> bytes:
    return generate_docx_bytes(SAMPLE_REVIEW)


# ---------------------------------------------------------------- 基本可用性


def test_generates_valid_docx(docx_bytes):
    """生成的必须是合法的 docx（ZIP 头 + 能被 python-docx 读回）。"""
    assert docx_bytes[:2] == b"PK", "docx 本质是 ZIP，文件头应为 PK"
    assert len(docx_bytes) > 10_000, "内容过少，可能生成失败"

    from docx import Document

    doc = Document(io.BytesIO(docx_bytes))
    assert len(doc.paragraphs) > 20


def test_document_has_all_sections(docx_bytes):
    """报告必须包含全部章节——缺章节的报告无法用于审批。"""
    from docx import Document

    doc = Document(io.BytesIO(docx_bytes))
    headings = [
        p.text.strip()
        for p in doc.paragraphs
        if p.style.name.startswith("Heading") and p.text.strip()
    ]
    joined = " ".join(headings)
    for expected in ["审查结论", "审查意见明细", "专家论证要点", "审查执行过程", "使用说明"]:
        assert expected in joined, f"缺少章节：{expected}"


def test_chinese_font_is_applied(docx_bytes):
    """中文字体必须设 eastAsia，否则 Word 里显示方框。"""
    from docx import Document

    doc = Document(io.BytesIO(docx_bytes))
    xml = doc.element.xml
    assert "eastAsia" in xml, "未设置中文字体（eastAsia）"


def test_has_page_number_footer(docx_bytes):
    """正式报告需要页码。"""
    from docx import Document

    doc = Document(io.BytesIO(docx_bytes))
    footer_xml = doc.sections[0].footer.paragraphs[0]._p.xml
    assert "PAGE" in footer_xml, "页脚未插入页码域"


# ---------------------------------------------------------------- 内容正确性


def test_conclusion_written(docx_bytes):
    """结论与评分需写入报告。

    评分在表格里、结论在段落里，两处都要查——
    只查段落会漏掉表格渲染的内容。
    """
    from docx import Document

    doc = Document(io.BytesIO(docx_bytes))
    text = "\n".join(p.text for p in doc.paragraphs)
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                text += "\n" + cell.text

    assert "修改后通过" in text, "结论未写入报告"
    assert "77" in text, "评分未写入报告"


def test_refusal_is_disclosed(docx_bytes):
    """核心要求：有拒答项时必须在报告开头显式告知。

    否则使用者可能以为「9 项全部已审查」，漏掉未判定的部分。
    """
    from docx import Document

    text = "\n".join(p.text for p in Document(io.BytesIO(docx_bytes)).paragraphs)
    assert "特别说明" in text, "拒答未在报告中显式告知"
    assert "2 项" in text or "两项" in text, "拒答数量未写明"
    assert "拒绝出具结论" in text or "拒绝出具判定" in text, "未说明拒答性质"


def test_citation_preserved(docx_bytes):
    """有依据的结论必须保留规范编号与条款号。"""
    from docx import Document

    text = "\n".join(p.text for p in Document(io.BytesIO(docx_bytes)).paragraphs)
    assert "第11条" in text, "规范条款号未保留"
    assert "已溯源" in text, "未标注引用已溯源"


def test_uncited_item_explains_why(docx_bytes):
    """无依据的条目必须说明原因，不能留空让人误以为是漏写。"""
    from docx import Document

    text = "\n".join(p.text for p in Document(io.BytesIO(docx_bytes)).paragraphs)
    assert "按证据闸门拒绝出具判定" in text or "未找到对应的现行有效条款依据" in text


def test_expert_points_included(docx_bytes):
    from docx import Document

    text = "\n".join(p.text for p in Document(io.BytesIO(docx_bytes)).paragraphs)
    assert "受力计算与设计验算论证" in text


def test_audit_trail_included(docx_bytes):
    """执行过程要可追溯——争议时能回答「这个结论怎么来的」。"""
    from docx import Document

    doc = Document(io.BytesIO(docx_bytes))
    table_text = ""
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                table_text += cell.text + " "
    assert "ComplianceReviewerAgent" in table_text
    # 表头列名是「Token」，大小写敏感，用原样匹配避免歧义
    assert "Token" in table_text, "Token 列未渲染"
    assert "2057" in table_text, "Agent 的 token 消耗未写入"


def test_disclaimer_present(docx_bytes):
    """必须声明报告不可替代法定审查程序。"""
    from docx import Document

    text = "\n".join(p.text for p in Document(io.BytesIO(docx_bytes)).paragraphs)
    assert "人工审查" in text or "人工复核" in text
    assert "不替代法定审查程序" in text


def test_section_reference_is_correct(docx_bytes):
    """章节交叉引用不能写错——写错会让人找不到对应内容。"""
    from docx import Document

    text = "\n".join(p.text for p in Document(io.BytesIO(docx_bytes)).paragraphs)
    # 拒答说明应指向「二、审查意见明细」，不是「三、」
    assert "二、审查意见明细" in text
    assert "详见三、审查意见明细" not in text


# ---------------------------------------------------------------- 落盘


def test_save_to_disk(tmp_path):
    out = tmp_path / "nested" / "report.docx"
    path = generate_word_report(SAMPLE_REVIEW, out)
    assert path.exists()
    assert path.stat().st_size > 10_000
    assert path.parent.exists(), "父目录未自动创建"


def test_handles_empty_review():
    """空审查结果不能崩溃——应生成一份空报告而非抛异常。"""
    empty = {
        "title": "空报告",
        "is_hazard_project": False,
        "is_over_scale": False,
        "hazard_categories": [],
        "overall_score": None,
        "overall_conclusion": None,
        "review_items": [],
        "expert_points": [],
        "agent_trace": [],
        "total_tokens": 0,
        "cost_estimate": 0,
    }
    data = generate_docx_bytes(empty)
    assert len(data) > 5_000

    from docx import Document

    doc = Document(io.BytesIO(data))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "本次审查未产生审查意见" in text

# ---------------------------------------------------------------- PDF 导出


def _pdf_text(data: bytes) -> str:
    """提取 PDF 文本并压掉换行。

    PDF 按版面宽度硬折行，「拒绝出具结论」可能被拆成
    「拒绝出具结」+「论」两行——断言必须先归一化，
    否则测试会因为排版宽度而失败，与内容对错无关。
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    raw = "".join((p.extract_text() or "") for p in reader.pages)
    return re.sub(r"\s+", "", raw)


class TestPdfReport:
    """PDF 是审查结论的最终交付物，必须与 Word 版内容一致。"""

    @pytest.fixture
    def pdf_bytes(self):
        from app.services.report_pdf import generate_pdf_bytes

        return generate_pdf_bytes(SAMPLE_REVIEW)

    def test_valid_pdf_header(self, pdf_bytes):
        assert pdf_bytes[:5] == b"%PDF-"
        assert b"%%EOF" in pdf_bytes[-64:]

    def test_chinese_glyphs_rendered(self, pdf_bytes):
        """中文字形必须真的写进 PDF。

        reportlab 内置字体不含 CJK，直接用会渲染成方框——
        这项测试保证降级字体路径不失效。
        """
        zh = sum(1 for c in _pdf_text(pdf_bytes) if "一" <= c <= "鿿")
        assert zh > 200, f"中文字符过少（{zh}），可能渲染为方框"

    def test_five_sections_present(self, pdf_bytes):
        txt = _pdf_text(pdf_bytes)
        for section in (
            "一、审查结论",
            "二、审查意见明细",
            "三、专家论证要点",
            "四、审查执行过程",
            "使用说明",
        ):
            assert section in txt, f"缺少章节：{section}"

    def test_refusals_disclosed(self, pdf_bytes):
        """拒答必须在报告开头显式披露。

        与 Word 版同一条硬要求：不告知会让使用者误以为「全部已审查」。
        """
        txt = _pdf_text(pdf_bytes)
        assert "特别说明" in txt
        assert "拒绝出具结论" in txt
        assert "人工复核" in txt

    def test_no_markup_literal_leak(self, pdf_bytes):
        """自加的 <b> 标签不能变成字面量显示在报告里。

        Paragraph 正文是 XML mini-language——
        如果无脑转义所有尖括号，加粗标签会原样印在报告上。
        """
        txt = _pdf_text(pdf_bytes)
        assert "<b>" not in txt, "加粗标签外露为字面量"
        assert "&amp;" not in txt, "转义实体外露为字面量"
        assert "&lt;" not in txt

    def test_special_chars_escaped(self):
        """法规原文含 & < > 时不能崩溃。"""
        from app.services.report_pdf import generate_pdf_bytes

        data = {
            "title": "T<&>测试",
            "review_items": [
                {
                    "item_title": "含特殊字符的检查项 <script> & \"引号\"",
                    "review_comment": "间距 & 大于 900mm < 不合规",
                    "regulation_reference": "JGJ 80-2016 <第4.2.1条>",
                    "compliance_status": "non_compliant",
                    "citation_verified": True,
                    "severity": "major",
                }
            ],
            "agent_trace": [],
        }
        assert generate_pdf_bytes(data)[:5] == b"%PDF-"

    def test_empty_review_not_crash(self):
        """零审查意见的报告也要能出（不能抛异常）。"""
        from app.services.report_pdf import generate_pdf_bytes

        data = {
            "title": "空报告",
            "review_items": [],
            "expert_points": [],
            "agent_trace": [],
        }
        assert generate_pdf_bytes(data)[:5] == b"%PDF-"

    def test_pdf_and_docx_disclose_same_refusal_count(self):
        """两种格式披露的拒答条数必须一致。

        不一致意味着审查结论在两种交付物里说法不同，
        这在合规审查里是不可接受的。
        """
        import re

        from app.services.report_pdf import generate_pdf_bytes

        refused = sum(
            1 for i in SAMPLE_REVIEW["review_items"] if i.get("refused")
        )
        if not refused:
            pytest.skip("样本数据中无拒答项")

        txt = _pdf_text(generate_pdf_bytes(SAMPLE_REVIEW))
        m = re.search(r"本报告有\s*(\d+)\s*项", txt)
        assert m, "未找到拒答条数披露"
        assert int(m.group(1)) == refused
