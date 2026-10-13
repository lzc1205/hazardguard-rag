"""审查报告生成（Word / PDF）。

对应需求文档 8.4 节。

报告是审查结果的**交付出口**——审查意见再好，用户拿不到可用的文件
就等于没有产品。这里做两件事：
1. Word 报告：现场可直接打印签字，符合施工行业习惯
2. PDF 报告：便于线上流转

**设计原则**：报告里每个结论都必须带规范编号与条款号。
合规审查报告没有依据等于废纸，格式再好看也没用。
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

# 状态 → 中文与配色（与前端 constants.ts 保持一致）
STATUS_META = {
    "compliant": ("符合", RGBColor(0x67, 0xC2, 0x3A)),
    "non_compliant": ("不符合", RGBColor(0xF5, 0x6C, 0x6C)),
    "needs_confirmation": ("待人工确认", RGBColor(0xE6, 0xA2, 0x3C)),
    "not_applicable": ("不适用", RGBColor(0x90, 0x93, 0x99)),
}

SEVERITY_LABEL = {
    "critical": "严重",
    "major": "重要",
    "minor": "一般",
    "suggestion": "建议",
}

CONCLUSION_LABEL = {
    "pass": "通过",
    "pass_with_modification": "修改后通过",
    "fail": "不通过",
    "not_applicable": "非危大工程",
}


# ---------------------------------------------------------------- 样式工具


def _set_font(run, name: str = "宋体", size: int = 11, bold: bool = False,
              color: RGBColor | None = None) -> None:
    """中文字体必须同时设置 ascii 和 eastAsia，否则 Word 里显示为方框。"""
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    if color:
        run.font.color.rgb = color
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)


def _shade_cell(cell, hex_color: str) -> None:
    """给表格单元格加底色。python-docx 没有原生 API，需操作 XML。"""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def _add_footer(doc: Document) -> None:
    """页脚加页码——正式报告需要。"""
    for section in doc.sections:
        p = section.footer.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run("第 ")
        _set_font(run, size=9)

        # 用域代码插入页码
        fld_begin = OxmlElement("w:fldChar")
        fld_begin.set(qn("w:fldCharType"), "begin")
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = "PAGE"
        fld_end = OxmlElement("w:fldChar")
        fld_end.set(qn("w:fldCharType"), "end")

        run2 = p.add_run()
        _set_font(run2, size=9)
        run2._r.append(fld_begin)
        run2._r.append(instr)
        run2._r.append(fld_end)

        run3 = p.add_run(" 页")
        _set_font(run3, size=9)


# ---------------------------------------------------------------- 主生成器


class ReportGenerator:
    """把审查结果渲染为 Word 文档。"""

    def __init__(self, review_data: dict[str, Any]):
        """
        review_data 需包含：
        - title / is_hazard_project / is_over_scale / hazard_categories
        - overall_score / overall_conclusion
        - review_items[]
        - expert_points[]
        """
        self.data = review_data

    def build(self) -> Document:
        d = self.data
        doc = Document()

        # 全局字体
        style = doc.styles["Normal"]
        style.font.name = "宋体"
        style.font.size = Pt(11)
        style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

        self._add_header(doc)
        self._add_conclusion(doc)
        self._add_items(doc)
        self._add_expert_points(doc)
        self._add_audit_trail(doc)
        self._add_disclaimer(doc)

        _add_footer(doc)
        return doc

    # ------------------------------------------------------------ 各章节

    def _add_header(self, doc: Document) -> None:
        title = doc.add_heading(self.data.get("title") or "危大工程专项施工方案合规审查报告", 0)
        for run in title.runs:
            _set_font(run, "黑体", 18, bold=True)

        # 报告编号 + 生成时间
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        no = self.data.get("report_number") or self._gen_number()
        run = p.add_run(f"报告编号：{no}　　生成时间：{datetime.now():%Y-%m-%d %H:%M}")
        _set_font(run, size=9, color=RGBColor(0x80, 0x80, 0x80))

        # 工程与审查信息表
        table = doc.add_table(rows=4, cols=4)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER

        hazard = self.data.get("hazard_categories") or []
        rows = [
            ("工程名称", self.data.get("project_name") or "—",
             "危大类别", "、".join(hazard) or "—"),
            ("是否危大工程", "是" if self.data.get("is_hazard_project") else "否",
             "是否超规模", "是" if self.data.get("is_over_scale") else "否"),
            ("施工总承包单位", self.data.get("construction_unit") or "—",
             "监理单位", self.data.get("supervision_unit") or "—"),
            ("审查依据", "住建部令第37号、建办质〔2018〕31号、JGJ 相关规范",
             "审查方式", "多智能体自动审查 + 人工复核"),
        ]
        for i, (k1, v1, k2, v2) in enumerate(rows):
            self._fill_kv(table.cell(i, 0), k1, True)
            self._fill_kv(table.cell(i, 1), v1, False)
            self._fill_kv(table.cell(i, 2), k2, True)
            self._fill_kv(table.cell(i, 3), v2, False)

        doc.add_paragraph()

    def _fill_kv(self, cell, text: str, is_key: bool) -> None:
        cell.text = ""
        run = cell.paragraphs[0].add_run(str(text))
        _set_font(
            run,
            "黑体" if is_key else "宋体",
            10,
            bold=is_key,
        )
        if is_key:
            _shade_cell(cell, "F0F4F8")

    def _add_conclusion(self, doc: Document) -> None:
        doc.add_heading("一、审查结论", level=1)

        score = self.data.get("overall_score")
        conclusion = self.data.get("overall_conclusion") or ""
        label = CONCLUSION_LABEL.get(conclusion, "待复核")

        table = doc.add_table(rows=2, cols=4)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER

        items = self.data.get("review_items") or []
        refused = sum(1 for i in items if i.get("refused"))
        non_compliant = sum(1 for i in items if i.get("compliance_status") == "non_compliant")
        cited = sum(1 for i in items if i.get("citation_verified"))

        cells = [
            ("综合结论", label),
            ("合规评分", f"{score} 分" if score is not None else "—"),
            ("不符合项", f"{non_compliant} 项"),
            ("拒答项", f"{refused} 项"),
        ]
        for i, (k, v) in enumerate(cells):
            self._fill_kv(table.cell(0, i), k, True)
            self._fill_kv(table.cell(1, i), v, False)

        doc.add_paragraph()

        # 拒答说明——必须在报告里显式告知，不能让使用者误以为「全部已审查」
        if refused > 0:
            p = doc.add_paragraph()
            run = p.add_run(
                f"特别说明：本报告有 {refused} 项因知识库未检索到现行有效的直接条款依据，"
                f"系统按「无依据不出具判定」原则拒绝出具结论（详见「二、审查意见明细」），"
                f"该部分需由审查人员人工复核确认后方可作为审批依据。"
            )
            _set_font(run, size=10.5, color=RGBColor(0xB0, 0x6A, 0x00))
            p.paragraph_format.left_indent = Cm(0.5)

        p = doc.add_paragraph()
        run = p.add_run(
            f"本报告共出具审查意见 {len(items)} 项，其中 {cited} 项已绑定规范条款依据。"
        )
        _set_font(run, size=10.5)

    def _add_items(self, doc: Document) -> None:
        doc.add_heading("二、审查意见明细", level=1)

        items = self.data.get("review_items") or []
        if not items:
            doc.add_paragraph("本次审查未产生审查意见。")
            return

        for idx, item in enumerate(items, 1):
            status = item.get("compliance_status", "")
            status_text, status_color = STATUS_META.get(status, (status, None))

            # 标题行
            h = doc.add_paragraph()
            h.paragraph_format.space_before = Pt(10)
            run = h.add_run(f"{idx}. {item.get('item_title', '')}")
            _set_font(run, "黑体", 12, bold=True)

            # 元信息行
            meta = doc.add_paragraph()
            meta.paragraph_format.left_indent = Cm(0.5)
            parts = [f"【{status_text}】"]
            if item.get("severity"):
                parts.append(
                    f"【{SEVERITY_LABEL.get(item['severity'], item['severity'])}】"
                )
            if item.get("agent_source"):
                parts.append(f"（{item['agent_source']}）")
            run = meta.add_run("  ".join(parts))
            _set_font(run, size=10, color=status_color, bold=True)

            # 审查意见
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.5)
            run = p.add_run("审查意见：")
            _set_font(run, "黑体", 10.5, bold=True)
            run = p.add_run(item.get("review_comment", ""))
            _set_font(run, size=10.5)

            # 法规依据——报告的核心，缺了就失去意义
            ref = item.get("regulation_reference")
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.5)
            run = p.add_run("法规依据：")
            _set_font(run, "黑体", 10.5, bold=True)
            if ref:
                run = p.add_run(str(ref))
                _set_font(run, size=10.5, color=RGBColor(0x1A, 0x5F, 0xB4))
                if item.get("citation_verified"):
                    run2 = p.add_run("（已溯源）")
                    _set_font(run2, size=9.5, color=RGBColor(0x67, 0xC2, 0x3A))
            else:
                run = p.add_run(
                    item.get("refuse_reason")
                    or "知识库中未找到对应的现行有效条款依据，系统拒绝出具判定"
                )
                _set_font(run, size=10.5, color=RGBColor(0xB0, 0x6A, 0x00))

            # 修改建议
            if item.get("modification_suggestion"):
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Cm(0.5)
                run = p.add_run("修改建议：")
                _set_font(run, "黑体", 10.5, bold=True)
                run = p.add_run(item["modification_suggestion"])
                _set_font(run, size=10.5, color=RGBColor(0x2F, 0x6F, 0xBB))

    def _add_expert_points(self, doc: Document) -> None:
        points = self.data.get("expert_points") or []
        if not points:
            return

        doc.add_heading("三、专家论证要点", level=1)
        doc.add_paragraph(
            "本工程属于超过一定规模的危险性较大的分部分项工程，"
            "按规定应组织召开专家论证会。以下为系统生成的论证要点，"
            "供专家组参考，最终以专家组论证意见为准。"
        )

        for idx, p in enumerate(points, 1):
            para = doc.add_paragraph()
            para.paragraph_format.space_before = Pt(8)
            run = para.add_run(f"{idx}. {p.get('point_title', '')}")
            _set_font(run, "黑体", 11, bold=True)

            content = doc.add_paragraph()
            content.paragraph_format.left_indent = Cm(0.5)
            run = content.add_run(p.get("point_content", ""))
            _set_font(run, size=10.5)

            if p.get("related_regulation"):
                r = doc.add_paragraph()
                r.paragraph_format.left_indent = Cm(0.5)
                run = r.add_run(f"依据：{p['related_regulation']}")
                _set_font(run, size=9.5, color=RGBColor(0x80, 0x80, 0x80))

    def _add_audit_trail(self, doc: Document) -> None:
        """执行过程记录。

        对应 JD 可观测性要求——让审查过程可追溯，
        出现争议时能回答「这个结论怎么来的」。
        """
        trace = self.data.get("agent_trace") or []
        if not trace:
            return

        doc.add_heading("四、审查执行过程", level=1)

        table = doc.add_table(rows=len(trace) + 1, cols=4)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER

        headers = ["环节", "耗时", "Token", "结果摘要"]
        for i, h in enumerate(headers):
            self._fill_kv(table.cell(0, i), h, True)

        for row, t in enumerate(trace, 1):
            vals = [
                t.get("agent", ""),
                f"{t.get('duration_ms', 0)} ms",
                str(t.get("tokens", 0)),
                (t.get("output_summary", "") or "")[:60],
            ]
            for col, v in enumerate(vals):
                self._fill_kv(table.cell(row, col), v, False)

        doc.add_paragraph()
        cost = self.data.get("cost_estimate")
        tokens = self.data.get("total_tokens")
        if cost is not None or tokens is not None:
            p = doc.add_paragraph()
            run = p.add_run(
                f"本次审查共消耗 {tokens or 0} tokens，成本估算 ¥{cost or 0:.4f}。"
            )
            _set_font(run, size=9.5, color=RGBColor(0x80, 0x80, 0x80))

    def _add_disclaimer(self, doc: Document) -> None:
        doc.add_paragraph()
        h = doc.add_heading("使用说明", level=1)
        notes = [
            "本报告由 HazardGuard-RAG 系统自动生成，须经具备相应资格的人工审查人员复核确认后方可作为审批依据。",
            "报告中标注「待人工确认」「拒答」的项目，表示系统未能在知识库中检索到现行有效的直接条款依据，"
            "系统按「无可靠依据不出具判定」原则未作结论，需人工查阅规范原文后判定。",
            "系统引用依据以现行有效版本为准。法规、标准可能修订，使用前请核对最新版本。",
            "本报告不替代法定审查程序，不作为施工安全决策的唯一依据。",
        ]
        for i, n in enumerate(notes, 1):
            p = doc.add_paragraph(style="List Number")
            run = p.add_run(n)
            _set_font(run, size=10, color=RGBColor(0x60, 0x60, 0x60))

    @staticmethod
    def _gen_number() -> str:
        return f"HG-{datetime.now():%Y%m%d-%H%M%S}"


# ---------------------------------------------------------------- 对外接口


def generate_word_report(review_data: dict[str, Any], output_path: str | Path) -> Path:
    """生成 Word 报告并落盘，返回文件路径。"""
    doc = ReportGenerator(review_data).build()
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def generate_docx_bytes(review_data: dict[str, Any]) -> bytes:
    """生成 Word 报告的字节流（用于 API 直接下载）。"""
    buf = io.BytesIO()
    ReportGenerator(review_data).build().save(buf)
    return buf.getvalue()