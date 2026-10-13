"""文本分块器：条款级切分。

危大工程规范文档的特点是「条文 + 编号」结构化程度高（如 JGJ 80-2016 第 3.1.2 条），
因此分块不能简单按固定长度硬切——必须尊重条文边界，否则检索出来的片段
无法绑定规范编号，也就谈不上「引用溯源」。

策略：
1. 优先按条文正则切分（强制性条文必须完整成块）
2. 块内再按超长阈值做二次滑动切分，并保留 overlap
3. 每块继承所属条文的元数据（规范号、条号、章节、是否强制性）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import settings

# 条文标题。建筑规范实际存在两种编号写法，必须都认：
#   1) 「第3.1.2 条」—— GB/部分行业标准
#   2) 「3.1.2」裸编号行首形式 —— JGJ 系列规范最常见
# 只匹配其中一种会导致整篇文档退化成单块，检索直接失效。
CLAUSE_PATTERN = re.compile(
    r"(?:^|\n)"
    r"(?:第\s*(?P<no_bare>\d+(?:\.\d+)+)\s*条(?P<title_bare>[^\n。；]{0,60}?)?"
    r"|(?P<no_num>\d+(?:\.\d+)+)\s*(?P<title_num>[^\n。；]{0,60}?))"
    r"(?P<body>[\s\S]*?)?"
    r"(?=(?:^|\n)(?:第\s*\d+(?:\.\d+)*\s*条|\d+(?:\.\d+)+\s*[^\n。；]{0,60})|\Z)",
    re.MULTILINE,
)

# 强制性条文关键字（建筑施工领域通用表述）
MANDATORY_KEYWORDS = (
    "严禁", "必须", "不得", "应当", "应", " shall", "必须使用", "严禁使用",
)

# 章节目录，如「4.2 模板支撑」
SECTION_PATTERN = re.compile(r"^(?P<no>\d+(?:\.\d+)*)\s+(?P<title>.{0,40})$")


@dataclass
class TextChunk:
    """一个检索单元。content 用于向量化，metadata 用于溯源展示。"""

    content: str
    doc_id: str
    doc_name: str
    clause_no: str | None = None
    section: str | None = None
    page: int | None = None
    is_mandatory: bool = False
    version: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def citation(self) -> str:
        """人可读的引用串，如「《建筑施工模板安全技术规范》JGJ 80-2016 第 3.1.2 条」。"""
        parts = []
        if self.doc_name:
            parts.append(f"《{self.doc_name}》")
        if self.version:
            parts.append(self.version)
        if self.clause_no:
            parts.append(self.clause_no)
        return " ".join(parts) if parts else self.doc_name or "未知来源"


def _is_mandatory(text: str) -> bool:
    return any(k in text for k in MANDATORY_KEYWORDS)


def _split_long(text: str, chunk_size: int, overlap: int) -> list[str]:
    """超长条文的二次切分，按句子边界下刀，尽量不断在句子中间。"""
    if len(text) <= chunk_size:
        return [text]

    sentences = re.split(r"(?<=[。；！？])", text)
    pieces: list[str] = []
    buf = ""
    for sent in sentences:
        if len(buf) + len(sent) <= chunk_size:
            buf += sent
            continue
        if buf:
            pieces.append(buf)
        # 单句就超长（罕见），硬切
        while len(sent) > chunk_size:
            pieces.append(sent[:chunk_size])
            sent = sent[chunk_size - overlap :]
        buf = sent
    if buf:
        pieces.append(buf)
    return pieces


def chunk_document(
    raw_text: str,
    doc_id: str,
    doc_name: str,
    version: str | None = None,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[TextChunk]:
    """把一份规范全文切成带溯源元数据的条款级块。

    Args:
        raw_text: PDF/Word 解析出的全文
        doc_id: 文档唯一标识
        doc_name: 规范名称，如「建筑施工模板安全技术规范」
        version: 版本号，如「JGJ 80-2016」
    """
    chunk_size = chunk_size or settings.DEFAULT_CHUNK_SIZE
    overlap = overlap or settings.DEFAULT_CHUNK_OVERLAP

    text = raw_text.replace("\r\n", "\n").replace("　", " ")
    matches = list(CLAUSE_PATTERN.finditer(text))

    # 未能匹配到条文结构（可能是说明性文件），退化为等长滑窗切分
    if not matches:
        chunks: list[TextChunk] = []
        step = max(chunk_size - overlap, 1)
        for i in range(0, len(text), step):
            seg = text[i : i + chunk_size].strip()
            # 阈值从 30 放宽到 8：短条款往往正是最关键的判定依据。
            # 实测踩过：`附件2-暗挖工程` 全文仅 26 字
            # （「采用矿山法、盾构法、顶管法施工的隧道、洞室工程。」）
            # 被这道门槛整个丢弃 —— 该条款**从未进入索引**，
            # 规则加权再强也救不回来（加权只能重排已进入候选池的文档）。
            # 8 字是安全下界：低于此长度基本是切分产生的噪声碎片。
            if len(seg) < 8:
                continue
            chunks.append(
                TextChunk(
                    content=seg,
                    doc_id=doc_id,
                    doc_name=doc_name,
                    version=version,
                    is_mandatory=_is_mandatory(seg),
                    extra={"chunk_index": len(chunks)},
                )
            )
        return chunks

    chunks = []
    current_section: str | None = None

    for m in matches:
        start_of_body = m.start("body") if m.group("body") is not None else m.end()
        prefix = text[: m.start()]
        last_section = None
        for line in reversed(prefix.split("\n")):
            line = line.strip()
            if not line:
                continue
            sm = SECTION_PATTERN.match(line)
            if sm:
                last_section = f"{sm.group('no')} {sm.group('title')}"
                break
            if len(line) < 40:
                break
        if last_section:
            current_section = last_section

        clause_no = re.sub(r"\s+", "", m.group("no_bare") or m.group("no_num") or "")
        clause_title = (m.group("title_bare") or m.group("title_num") or "").strip()
        # 统一补上「第…条」后缀，让引用串格式一致，便于前端展示与人工核对
        display_no = clause_no if clause_no.startswith("第") else f"第{clause_no}条"
        body = (m.group("body") or "").strip()
        full = f"{display_no} {clause_title}\n{body}".strip()

        for piece in _split_long(full, chunk_size, overlap):
            if len(piece.strip()) < 20:
                continue
            chunks.append(
                TextChunk(
                    content=piece,
                    doc_id=doc_id,
                    doc_name=doc_name,
                    version=version,
                    clause_no=display_no,
                    section=current_section,
                    is_mandatory=_is_mandatory(piece),
                    extra={"clause_title": clause_title, "chunk_index": len(chunks)},
                )
            )

    return chunks


def dedupe_chunks(chunks: list[TextChunk]) -> list[TextChunk]:
    """按 content 哈希去重，防止同一条文在多页重复入库污染召回。"""
    seen: set[str] = set()
    result = []
    for c in chunks:
        key = re.sub(r"\s+", "", c.content)
        if key in seen:
            continue
        seen.add(key)
        result.append(c)
    return result


def chunk_parsed_doc(
    parsed,
    doc_id: str,
    doc_name: str | None = None,
    version: str | None = None,
) -> list[TextChunk]:
    """从解析结果分块，并回填页码。

    页码是溯源的硬要求——审查报告里要写「JGJ 80-2016 第3.1.2 条（第 8 页）」，
    让人能翻到原文核对。做法是逐页定位每个条文块的起始偏移量，
    落在哪一页就标哪一页；跨页条文取起始页。
    """
    name = doc_name or parsed.doc_name
    all_chunks: list[TextChunk] = []
    offset = 0
    page_spans: list[tuple[int, int]] = []  # (起始偏移, 页码)
    for page in parsed.pages:
        page_spans.append((offset, page.page_no))
        offset += len(page.text) + 1  # 与拼接时的 "\n" 对齐

    for page in parsed.pages:
        all_chunks.extend(chunk_document(page.text, doc_id, name, version))

    # 回填页码：按块内容在全文中的首次出现位置判断
    full = parsed.full_text

    def locate(chunk: TextChunk) -> int:
        key = re.sub(r"\s+", "", chunk.content)[:24]
        return full.find(key) if key else -1

    for c in all_chunks:
        pos = locate(c)
        if pos < 0:
            c.page = None
            continue
        page_no = None
        for start, no in page_spans:
            if pos >= start:
                page_no = no
            else:
                break
        c.page = page_no

    return all_chunks