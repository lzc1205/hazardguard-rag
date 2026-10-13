"""引用溯源与证据校验。

合规审查系统的底线：**没有可靠依据就不能给结论**。
普通 RAG 拿到 top-k 就开始编造答案，而这里必须先过证据闸门——
证据不足时返回「拒答」，而不是退而求其次地编一个看起来合理的答案。

四道校验：
1. 得分阈值   —— 检索结果整体太弱，说明知识库根本没覆盖这个问题
2. 证据密度   —— 命中的条款里是否真有强制性条文（没有则风险等级需下调）
3. 条款可查   —— 每个结论必须绑定 doc_id + 条款号 + 原文片段
4. 置信度映射 —— 综合得分转成高/中/低，低置信度强制人工复核
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.rag.retriever import ScoredChunk


class RiskLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    COMPLIANT = "compliant"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Citation:
    """一条可溯源的引用。这是给用户看「依据在哪」的载体。"""

    doc_name: str
    version: str | None
    clause_no: str | None
    section: str | None
    page: int | None
    quote: str
    is_mandatory: bool
    score: float

    def to_dict(self) -> dict:
        parts = [f"《{self.doc_name}》"]
        if self.version:
            parts.append(self.version)
        if self.clause_no:
            parts.append(self.clause_no)
        return {
            "citation": " ".join(parts),
            "doc_name": self.doc_name,
            "version": self.version,
            "clause_no": self.clause_no,
            "section": self.section,
            "page": self.page,
            "quote": self.quote,
            "is_mandatory": self.is_mandatory,
            "score": round(self.score, 4),
        }


@dataclass
class Evidence:
    """证据包：判断风险等级的全部依据。"""

    citations: list[Citation] = field(default_factory=list)
    has_mandatory_hit: bool = False
    top_score: float = 0.0
    confidence: Confidence = Confidence.LOW
    passed: bool = False
    reject_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "reject_reason": self.reject_reason,
            "confidence": self.confidence.value,
            "has_mandatory_hit": self.has_mandatory_hit,
            "top_score": round(self.top_score, 4),
            "citations": [c.to_dict() for c in self.citations],
        }


# 得分阈值：用 RRF 融合分时，top1 低于这个值基本等于没命中
MIN_TOP_SCORE = 0.005
MIN_CITATIONS = 1


def build_evidence(items: list[ScoredChunk], top_k: int = 5) -> Evidence:
    """把检索结果转成可用的证据包，不通过则标记为拒答。"""
    ev = Evidence()
    if not items:
        ev.reject_reason = "知识库中未检索到任何相关条款，无法给出合规判定"
        return ev

    top = items[:top_k]
    ev.top_score = top[0].final_score

    for item in top:
        c = item.chunk
        ev.citations.append(
            Citation(
                doc_name=c.doc_name,
                version=c.version,
                clause_no=c.clause_no,
                section=c.section,
                page=c.page,
                quote=c.content[:300],
                is_mandatory=c.is_mandatory,
                score=item.final_score,
            )
        )
        if c.is_mandatory:
            ev.has_mandatory_hit = True

    if ev.top_score < MIN_TOP_SCORE:
        ev.reject_reason = (
            f"检索置信度过低（top1={ev.top_score:.4f} < {MIN_TOP_SCORE}），"
            "相关规范可能未收录，请人工核实"
        )
        return ev

    if len(ev.citations) < MIN_CITATIONS:
        ev.reject_reason = "有效依据数量不足，无法出具判定"
        return ev

    # 强制绑定：每条引用都必须能定位回原文
    for cit in ev.citations:
        if not cit.doc_name or not cit.quote.strip():
            ev.reject_reason = "存在无法定位原文的引用，判定已拦截"
            return ev

    ev.confidence = _map_confidence(ev.top_score, len(ev.citations))
    ev.passed = True
    return ev


def _map_confidence(top_score: float, n_citations: int) -> Confidence:
    # 阈值按 RRF 量纲设定：RRF 分上限约为 2/(k+1)≈0.033，
    # 因此 top1 超过 0.02 已是强命中；单条强命中也可给高置信度。
    if top_score >= 0.02 and n_citations >= 2:
        return Confidence.HIGH
    if top_score >= 0.008 or (top_score >= 0.015 and n_citations >= 1):
        return Confidence.MEDIUM
    return Confidence.LOW


def assess_risk(evidence: Evidence, keywords: list[str] | None = None) -> RiskLevel:
    """依据证据给出风险等级。

    关键词命中只是辅助信号，主判定依据仍是证据强度——
    避免"模型觉得像有问题"就判高风险。
    """
    if not evidence.passed:
        return RiskLevel.COMPLIANT

    kw_hits = 0
    text = " ".join(c.quote for c in evidence.citations)
    for kw in keywords or []:
        if kw and kw in text:
            kw_hits += 1

    if evidence.has_mandatory_hit and kw_hits >= 2:
        return RiskLevel.HIGH
    if evidence.has_mandatory_hit or kw_hits >= 2:
        return RiskLevel.MEDIUM
    if kw_hits == 1:
        return RiskLevel.LOW
    return RiskLevel.COMPLIANT


NO_EVIDENCE_TEMPLATE = (
    "无法就该问题给出合规判定。\n"
    "原因：{reason}\n"
    "建议：请补充具体条款依据，或由人工复核确认。"
)


def render_no_evidence(evidence: Evidence) -> str:
    return NO_EVIDENCE_TEMPLATE.format(reason=evidence.reject_reason or "未知原因")