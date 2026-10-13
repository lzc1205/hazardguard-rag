"""PDF 报告生成器。

与 Word 版（report_service.py）共用同一份数据结构，
保证两种格式的内容、结论、拒答披露完全一致——
**不允许出现「Word 版说拒答了N 项，PDF 版没提」这种不一致**。

技术要点：

**中文字体**
reportlab 的内置字体（Helvetica 等）不含 CJK 字形，
直接用会渲染成方框。必须注册中文 TTF；
本项目优先走 reportlab 自带的 CMap 字体，
找不到系统字体时降级为「明确报错」而不是静默输出乱码——
输出一个满屏方框的 PDF 比报错更糟。

**为什么不用 WeasyPrint / wkhtmltopdf**
它们依赖系统级 HTML 渲染引擎，装在 Windows 上要额外配 GTK，
且启动慢、内存占用高。reportlab 是纯 Python 方案，
PDF 是审查结论的最终交付物，必须在任何机器上都能生成。
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ---------------------------------------------------------------- 颜色与字号

BRAND = colors.HexColor("#2B6CB0")
BRAND_LIGHT = colors.HexColor("#EBF4FF")
DANGER = colors.HexColor("#C0392B")
DANGER_LIGHT = colors.HexColor("#FDECEA")
WARN = colors.HexColor("#B7791F")
OK_GREEN = colors.HexColor("#276749")
GREY = colors.HexColor("#4A5568")
GREY_LIGHT = colors.HexColor("#F7FAFC")
BORDER = colors.HexColor("#E2E8F0")

STATUS_LABEL = {
    "compliant": "符合",
    "non_compliant": "不符合",
    "needs_confirmation": "待人工确认",
    "not_applicable": "不适用",
}

CONCLUSION_LABEL = {
    "pass": "通过",
    "pass_with_modification": "修改后通过",
    "fail": "不通过",
    "pending": "待定",
}

SEVERITY_LABEL = {
    "critical": "严重",
    "major": "重要",
    "minor": "一般",
    "suggestion": "建议",
}

_font_ready = False


def ensure_chinese_font() -> str:
    """注册中文字体，返回字体名。

    优先级：
    1. reportlab 内置 CJK 字体（STSong-Light）—— 零依赖，任何机器都能用
    2. 系统 TTF（Windows msy.ttc / 思源黑体等）—— 更好看但依赖环境
    """
    global _font_ready
    if _font_ready:
        return "STSong-Light"

    # 1) 内置 CMap 字体，纯 Python 环境也能出中文
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        _font_ready = True
        return "STSong-Light"
    except Exception:  # noqa: BLE001
        pass

    # 2) 退化到系统字体
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            if path.endswith((".ttc", ".TTC")):
                # TTC 是字体集合，reportlab 需指定子字体索引
                pdfmetrics.registerFont(TTFont("CN", path, subfontIndex=0))
            else:
                pdfmetrics.registerFont(TTFont("CN", path))
            _font_ready = True
            return "CN"
        except Exception:  # noqa: BLE001
            continue

    raise RuntimeError(
        "未找到可用的中文字体，PDF 会渲染成方框。"
        "请安装 SimHei/SimSun，或改用 Word 导出。"
    )


def _styles(font: str) -> dict[str, ParagraphStyle]:
    """统一样式表，避免各处硬编码字号。

    用 dict.update 覆盖而不是 `**base, key=value`——
    后者在 base 已含该键时会报「got multiple values for keyword argument」。
    """
    def mk(name: str, **kw) -> ParagraphStyle:
        opts = {"fontName": font, "textColor": colors.HexColor("#1A202C")}
        opts.update(kw)
        return ParagraphStyle(name, **opts)

    return {
        "title": mk("t", fontSize=18, leading=26,
                    alignment=TA_CENTER, spaceAfter=4),
        "subtitle": mk("st", fontSize=9.5, leading=14,
                       alignment=TA_CENTER, textColor=GREY),
        "h1": mk("h1", fontSize=13, leading=20,
                 spaceBefore=14, spaceAfter=7, textColor=BRAND),
        "h2": mk("h2", fontSize=11.5, leading=18,
                 spaceBefore=10, spaceAfter=5),
        "body": mk("b", fontSize=9.5, leading=16,
                   alignment=TA_JUSTIFY, spaceAfter=4),
        "small": mk("s", fontSize=8.5, leading=13, textColor=GREY),
        "cell": mk("c", fontSize=9, leading=13),
        "cell_head": mk("ch", fontSize=9, leading=13,
                        textColor=colors.white, alignment=TA_LEFT),
        "center": mk("ct", fontSize=9, leading=13, alignment=TA_CENTER),
    }


class PdfReportGenerator:
    """PDF 报告渲染器。

    章节结构与 Word 版完全一致：
    一、审查结论 / 二、审查意见明细 / 三、专家论证要点
    四、审查执行过程 / 使用说明
    """

    def __init__(self, review_data: dict[str, Any]):
        self.d = review_data
        self.font = ensure_chinese_font()
        self.s = _styles(self.font)

    # ------------------------------------------------------------ 内部工具

    def _p(self, text: str, style: str = "body") -> Paragraph:
        """渲染段落。

        **只转义数据，不动 <b> 这类我们自己加的标签**：
        Paragraph 的正文是 XML mini-language，直接把所有尖括号转义掉，
        会让 <b>加粗</b> 变成字面量 "<b>加粗</b>" 显示出来。

        做法：先把合法标签换成占位符 → 转义全文 → 再换回标签。
        """
        ALLOWED = ("b", "/b", "i", "/i", "br/", "br")
        raw = str(text or "")

        holder = "\x00{}\x00"
        for i, tag in enumerate(ALLOWED):
            raw = raw.replace(f"<{tag}>", holder.format(f"T{i}"))

        raw = (
            raw.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

        for i, tag in enumerate(ALLOWED):
            raw = raw.replace(holder.format(f"T{i}"), f"<{tag}>")

        return Paragraph(raw, self.s[style])

    def _kv_table(self, rows: list[tuple[str, Any]], widths: list[float]) -> Table:
        """两列信息表。"""
        data = [
            [self._p(k, "cell"), self._p(v, "cell")]
            for k, v in rows
        ]
        t = Table(data, colWidths=widths)
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), GREY_LIGHT),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return t

    def _grid(self, header: list[str], rows: list[list[Any]],
              widths: list[float]) -> Table:
        """带表头的多列表格。"""
        data = [[self._p(h, "cell_head") for h in header]]
        for r in rows:
            data.append([self._p(c, "cell") for c in r])

        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                     [colors.white, GREY_LIGHT]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return t

    def _callout(self, text: str, bg, fg, border) -> Table:
        """提示框（用于拒答披露这类必须醒目的内容）。"""
        t = Table([[self._p(text, "body")]], colWidths=[165 * mm])
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), bg),
                    ("BOX", (0, 0), (-1, -1), 0.8, border),
                    ("LEFTPADDING", (0, 0), (-1, -1), 9),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("TEXTCOLOR", (0, 0), (-1, -1), fg),
                ]
            )
        )
        return t

    # ------------------------------------------------------------ 章节

    def _header(self, story: list) -> None:
        d = self.d
        story.append(self._p(d.get("title") or "危大工程合规审查报告", "title"))
        story.append(
            self._p(
                f"报告编号：{d.get('report_number') or PdfReportGenerator._gen_number()}",
                "subtitle",
            )
        )
        story.append(Spacer(1, 10))

    def _conclusion(self, story: list) -> None:
        d = self.d
        story.append(self._p("一、审查结论", "h1"))

        items = d.get("review_items") or []
        refused = sum(1 for i in items if i.get("refused"))
        non_comp = sum(1 for i in items if i.get("compliance_status") == "non_compliant")
        cited = sum(1 for i in items if i.get("citation_verified"))

        cat = d.get("hazard_categories") or []
        cat_text = "、".join(cat) if cat else "—"

        story.append(
            self._kv_table(
                [
                    ("综合结论", CONCLUSION_LABEL.get(
                        d.get("overall_conclusion") or "", "待定")),
                    ("合规评分", f"{d.get('overall_score')} 分"
                     if d.get("overall_score") is not None else "—"),
                    ("是否危大工程", "是" if d.get("is_hazard_project") else "否"),
                    ("是否超过一定规模", "是" if d.get("is_over_scale") else "否"),
                    ("危大类别", cat_text),
                    ("审查意见总数", f"{len(items)} 条"),
                    ("其中有法规依据", f"{cited} 条"),
                    ("不符合项", f"{non_comp} 条"),
                    ("证据不足拒答", f"{refused} 条"),
                ],
                [42 * mm, 123 * mm],
            )
        )
        story.append(Spacer(1, 8))

        # 拒答必须在报告开头显式披露——
        # 否则使用者会以为「所有项都已审查」，漏掉未判定部分。
        if refused:
            story.append(
                self._callout(
                    f"<b>特别说明</b>：本报告有 {refused} 项因知识库未检索到"
                    f"现行有效的直接条款依据，系统按「无依据不出具判定」原则"
                    f"拒绝出具结论（详见二、审查意见明细）。该部分需由审查人员"
                    f"人工复核确认后方可作为审批依据。",
                    DANGER_LIGHT, DANGER, DANGER,
                )
            )
            story.append(Spacer(1, 6))

    def _items(self, story: list) -> None:
        story.append(self._p("二、审查意见明细", "h1"))
        items = self.d.get("review_items") or []
        if not items:
            story.append(self._p("本次审查未产生审查意见。", "small"))
            return

        for idx, it in enumerate(items, 1):
            status = it.get("compliance_status") or ""
            parts: list[Any] = [
                self._p(
                    f"{idx}. {it.get('item_title') or '未命名检查项'}"
                    f"　[{STATUS_LABEL.get(status, status)}]",
                    "h2",
                )
            ]

            ref = it.get("regulation_reference")
            if ref:
                cite = "已溯源" if it.get("citation_verified") else "未通过引用校验"
                parts.append(
                    self._p(f"<b>法规依据</b>：{ref}（{cite}）", "small")
                )
            else:
                parts.append(
                    self._p(
                        f"<b>法规依据</b>：{it.get('refuse_reason') or '知识库无对应条款依据'}"
                        "（系统拒答）",
                        "small",
                    )
                )

            comment = it.get("review_comment")
            if comment:
                parts.append(self._p(f"<b>审查意见</b>：{comment}", "body"))

            sev = SEVERITY_LABEL.get(it.get("severity") or "", "")
            sug = it.get("modification_suggestion")
            if sug and sug != "无需修改":
                parts.append(
                    self._p(f"<b>修改建议</b>（{sev or '一般'}）：{sug}", "body")
                )

            parts.append(Spacer(1, 4))
            # KeepTogether：单条意见不跨页断开，否则读起来割裂
            story.append(KeepTogether(parts))

    def _expert_points(self, story: list) -> None:
        pts = self.d.get("expert_points") or []
        if not pts:
            return
        story.append(self._p("三、专家论证要点", "h1"))
        story.append(
            self._p(
                "本工程超过一定规模，需组织专家论证。以下要点供专家组参考。",
                "small",
            )
        )
        story.append(Spacer(1, 5))
        rows = [
            [
                i,
                p.get("point_title") or "",
                (p.get("point_content") or "")[:220],
                SEVERITY_LABEL.get(p.get("risk_level") or "", p.get("risk_level") or ""),
            ]
            for i, p in enumerate(pts, 1)
        ]
        story.append(
            self._grid(
                ["#", "论证要点", "内容", "风险"],
                rows,
                [10 * mm, 40 * mm, 100 * mm, 15 * mm],
            )
        )

    def _audit_trail(self, story: list) -> None:
        trace = self.d.get("agent_trace") or []
        if not trace:
            return
        story.append(self._p("四、审查执行过程", "h1"))
        story.append(
            self._p(
                "记录每个 Agent 的执行情况，便于复核结论的生成过程。",
                "small",
            )
        )
        story.append(Spacer(1, 5))

        rows = []
        for t in trace:
            name = t.get("agent") or "-"
            flags = []
            if t.get("degraded"):
                flags.append("降级")
            if t.get("refusals"):
                flags.append(f"拒答{t['refusals']}")
            if t.get("error"):
                flags.append("异常")
            rows.append([
                name,
                f"{t.get('duration_ms', 0)}ms",
                t.get("tokens", 0),
                "、".join(flags) or "正常",
            ])

        story.append(
            self._grid(
                ["执行环节", "耗时", "Token", "状态"],
                rows,
                [72 * mm, 22 * mm, 22 * mm, 49 * mm],
            )
        )
        story.append(Spacer(1, 5))
        story.append(
            self._p(
                f"累计 Token：{self.d.get('total_tokens', 0)}　|　"
                f"成本估算：¥{self.d.get('cost_estimate', 0)}",
                "small",
            )
        )

    def _disclaimer(self, story: list) -> None:
        story.append(self._p("使用说明", "h1"))
        for i, txt in enumerate(
            [
                "本报告由系统依据现行法规库自动生成，供审查人员参考。",
                "报告结论仅对送审文本负责，方案修改后应重新审查。",
                "标记为「系统拒答」的条目表示知识库中无现行有效条款依据，"
                "须由具备相应资格的人员人工判定，不得直接作为审批依据。",
                "本报告不替代法定审查程序，不作为安全生产责任认定依据。",
            ],
            1,
        ):
            story.append(self._p(f"{i}. {txt}", "small"))
            story.append(Spacer(1, 3))

    # ------------------------------------------------------------ 入口

    def build(self) -> bytes:
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
            title=self.d.get("title") or "合规审查报告",
            author="HazardGuard",
        )
        story: list[Any] = []
        self._header(story)
        self._conclusion(story)
        self._items(story)
        self._expert_points(story)
        self._audit_trail(story)
        self._disclaimer(story)

        doc.build(
            story,
            onFirstPage=self._decorate,
            onLaterPages=self._decorate,
        )
        return buf.getvalue()

    def _decorate(self, canvas, doc) -> None:
        """每页页脚：页码 + 免责提示。

        字体名取实例的 self.font——写死「STSong-Light」在降级到
        系统 TTF 的路径下会找不到字体直接崩。
        """
        canvas.saveState()
        canvas.setFont(self.font, 7.5)
        canvas.setFillColor(GREY)
        canvas.setStrokeColor(BORDER)
        canvas.line(20 * mm, 14 * mm, 190 * mm, 14 * mm)
        canvas.drawString(20 * mm, 10 * mm, "HazardGuard · 危大工程合规审查系统")
        canvas.drawRightString(190 * mm, 10 * mm, f"第 {doc.page} 页")
        canvas.restoreState()

    @staticmethod
    def _gen_number() -> str:
        from datetime import datetime

        return "HZG-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def generate_pdf_bytes(review_data: dict[str, Any]) -> bytes:
    """生成 PDF 并返回字节流。"""
    return PdfReportGenerator(review_data).build()


def generate_pdf_report(review_data: dict[str, Any], output_path: str | Path) -> Path:
    """生成 PDF 并落盘。"""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(generate_pdf_bytes(review_data))
    return p