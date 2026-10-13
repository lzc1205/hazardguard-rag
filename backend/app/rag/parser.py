"""文档结构化解析：把 PDF / Word / Excel 统一转成带页码的纯文本。

为什么不直接用 pypdf 抽文字：
1. 建筑规范 PDF 常见双栏排版，直接抽会把左右栏文字串行交错，条款被打乱；
2. 表格里的参数值（如"立杆间距 ≤900mm"）抽出来是碎片，无法作为审查依据；
3. 扫描件没有文本层，需要 OCR 兜底。

这里做三级降级：pdfplumber（版面还原最好）→ pypdf（快）→ 空文本告警。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParsedPage:
    """单页内容。page 从 1 开始，便于报告中标注页码溯源。"""

    page_no: int
    text: str
    has_text_layer: bool = True


@dataclass
class ParsedDoc:
    """解析结果。chunk 化时按 pages 铺开，页码才能对得上原文。"""

    doc_name: str
    file_type: str
    pages: list[ParsedPage] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n".join(p.text for p in self.pages)

    @property
    def is_scanned(self) -> bool:
        return bool(self.pages) and not any(p.has_text_layer for p in self.pages)


# 页眉页脚噪声：规范 PDF 每页重复的刊号、版权行
NOISE_PATTERNS = [
    re.compile(r"^\s*U\s*C\s*\d{2,4}\s*$"),
    re.compile(r"^\s*中华人民共和国住房和城乡建设部\s*$"),
    re.compile(r"^\s*国家 standard|国家标准\s*$"),
    re.compile(r"^\s*[—\-—_]{3,}\s*$"),
    re.compile(r"^\s*\d+\s*$"),  # 孤立页码
]


def _clean(text: str) -> str:
    """清理页眉页脚与空白，但不破坏条文编号。"""
    lines = [ln.rstrip() for ln in text.split("\n")]
    kept = [ln for ln in lines if not any(p.match(ln.strip()) for p in NOISE_PATTERNS)]
    # 合并3 个以上连续空行
    out: list[str] = []
    blanks = 0
    for ln in kept:
        if not ln.strip():
            blanks += 1
            if blanks > 2:
                continue
        else:
            blanks = 0
        out.append(ln)
    return "\n".join(out)


def parse_pdf(path: str | Path) -> ParsedDoc:
    """解析 PDF。优先 pdfplumber（能处理双栏与表格），失败降级 pypdf。"""
    path = Path(path)
    doc = ParsedDoc(doc_name=path.stem, file_type="pdf")

    try:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            doc.meta["page_count"] = len(pdf.pages)
            for i, page in enumerate(pdf.pages, start=1):
                # 双栏版式：先按 x 坐标切栏，左右各自从上到下，再拼起来
                text = _extract_with_columns(page)
                if not text.strip():
                    text = page.extract_text() or ""
                cleaned = _clean(text)
                doc.pages.append(
                    ParsedPage(page_no=i, text=cleaned, has_text_layer=bool(cleaned.strip()))
                )
        return doc
    except Exception as e:  # noqa: BLE001
        doc.warnings.append(f"pdfplumber 解析失败({e})，降级 pypdf")

    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        doc.meta["page_count"] = len(reader.pages)
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            cleaned = _clean(text)
            doc.pages.append(
                ParsedPage(page_no=i, text=cleaned, has_text_layer=bool(cleaned.strip()))
            )
    except Exception as e:  # noqa: BLE001
        doc.warnings.append(f"pypdf 解析失败: {e}")

    if doc.is_scanned:
        doc.warnings.append("疑似扫描件（无文本层），需 OCR 后才能入库")
    return doc


def _extract_with_columns(page) -> str:
    """双栏检测：页面中线附近有明显空隙则判定为双栏。

    建筑规范 PDF 常见双栏排版，直接抽会把左右栏文字串行交错，
    导致条文被打乱、检索失效。
    """
    width = page.width or 0
    if not width:
        return page.extract_text() or ""

    words = page.extract_words()
    if not words:
        return ""

    mid = width / 2
    tolerance = width * 0.06
    left = [w for w in words if (w["x0"] + w["x1"]) / 2 < mid - tolerance]
    right = [w for w in words if (w["x0"] + w["x1"]) / 2 > mid + tolerance]

    # 两侧都有足够多文字，且都不过半页宽，才认定为双栏
    if len(left) > 40 and len(right) > 40:
        def to_text(ws):
            ws = sorted(ws, key=lambda w: (round(w["top"] / 3), w["x0"]))
            lines: dict[int, list] = {}
            for w in ws:
                key = round(w["top"] / 3)
                lines.setdefault(key, []).append(w)
            return "\n".join(
                "".join(x["text"] for x in sorted(v, key=lambda t: t["x0"]))
                for _, v in sorted(lines.items())
            )

        return to_text(left) + "\n" + to_text(right)

    return page.extract_text() or ""


def parse_docx(path: str | Path) -> ParsedDoc:
    """解析 Word。保留标题层级，段落序号不能丢。"""
    from docx import Document

    path = Path(path)
    doc = ParsedDoc(doc_name=path.stem, file_type="docx")
    d = Document(str(path))
    parts: list[str] = []
    for para in d.paragraphs:
        txt = para.text.strip()
        if not txt:
            continue
        style = (para.style.name or "").lower() if para.style else ""
        # 标题降级为纯文本，但保留缩进层级信息供chunker 判断章节
        if "heading" in style or "标题" in style:
            parts.append(f"\n{txt}\n")
        else:
            parts.append(txt)

    # 表格单独拼接：审查参数值常在表里
    for table in d.tables:
        rows = []
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            parts.append("\n" + "\n".join(rows) + "\n")

    text = _clean("\n".join(parts))
    doc.pages.append(ParsedPage(page_no=1, text=text, has_text_layer=bool(text)))
    doc.meta["paragraph_count"] = len(d.paragraphs)
    return doc


def parse_txt(path: str | Path) -> ParsedDoc:
    path = Path(path)
    text = _clean(path.read_text(encoding="utf-8", errors="ignore"))
    return ParsedDoc(
        doc_name=path.stem,
        file_type="txt",
        pages=[ParsedPage(page_no=1, text=text, has_text_layer=bool(text))],
    )


SUPPORTED = {".pdf": parse_pdf, ".docx": parse_docx, ".txt": parse_txt}


def parse_file(path: str | Path) -> ParsedDoc:
    """统一入口。按后缀分派，不支持的类型直接报错而不是静默返回空。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    handler = SUPPORTED.get(path.suffix.lower())
    if not handler:
        raise ValueError(f"暂不支持的文件类型: {path.suffix}（支持 {', '.join(SUPPORTED)}）")
    return handler(path)