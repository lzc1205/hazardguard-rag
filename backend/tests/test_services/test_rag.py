"""RAG 检索层单元测试。

用 pytest 跑：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_services/test_rag.py -v

这些测试不依赖数据库和大模型，纯逻辑验证，可随时运行。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.rag.chunker import chunk_document, dedupe_chunks
from app.rag.evidence import (
    Confidence,
    Evidence,
    assess_risk,
    build_evidence,
    render_no_evidence,
)
from app.rag.retriever import (
    BM25Index,
    HybridRetriever,
    ScoredChunk,
    extract_clauses,
    extract_doc_names,
    rule_based_boost,
    tokenize,
)

SAMPLE_NORM = """建筑施工模板安全技术规范

4.2 模板支撑立杆

4.2.1 模板支撑立杆的纵横向间距，不应大于900mm。
4.2.2 模板支撑立杆高度超过4m时，应在顶部和底部设置水平剪刀撑。
第5.1.3 条 严禁使用木支撑搭设高度超过三米的模板支撑架。
"""


@pytest.fixture
def chunks():
    return chunk_document(
        SAMPLE_NORM,
        doc_id="jgj80",
        doc_name="建筑施工模板安全技术规范",
        version="JGJ 80-2016",
    )


# ------------------------- 分块 -------------------------

def test_chunk_splits_by_clause(chunks):
    """条文应被切成独立块，且带上方章节号。"""
    assert len(chunks) >= 3
    clause_nos = {c.clause_no for c in chunks}
    assert "第5.1.3条" in clause_nos


def test_chunk_detects_mandatory(chunks):
    """「严禁使用」所在条文必须被标记为强制性。"""
    mandatory = [c for c in chunks if c.is_mandatory]
    assert len(mandatory) >= 1
    assert any("严禁" in c.content for c in mandatory)


def test_chunk_carries_citation(chunks):
    """引用串必须能定位到规范号+条号，这是溯源的前提。"""
    for c in chunks:
        assert "《建筑施工模板安全技术规范》" in c.citation
        assert "JGJ 80-2016" in c.citation


def test_dedupe_removes_duplicates():
    dup = chunk_document(SAMPLE_NORM, "d", "文档")
    assert len(dedupe_chunks(dup + dup)) == len(dedupe_chunks(dup))


def test_chunk_handles_plain_text():
    """无法匹配条文结构的文档不能崩，应退化为滑窗切分。"""
    plain = "这是一段没有任何条文编号的普通说明文字。" * 20
    result = chunk_document(plain, "d1", "说明文件")
    assert isinstance(result, list)


# ------------------------- 检索 -------------------------

def test_tokenize_produces_bigrams():
    """中文需同时产出单字与 bigram，否则短语召回会退化。"""
    toks = tokenize("模板支撑立杆")
    assert "模板" in toks
    assert len(toks) > 3


def test_bm25_ranks_relevant_chunk_first(chunks):
    """BM25 应把包含查询词的条文排在前面。"""
    idx = BM25Index().build(chunks)
    hits = idx.search("模板支撑立杆间距", top_k=3)
    assert hits, "应有召回结果"
    top_idx, top_score = hits[0]
    assert "立杆" in chunks[top_idx].content
    assert top_score > 0


def test_hybrid_fusion_merges_both_paths(chunks):
    """双路命中同一块时，RRF 融合分应高于单路。"""
    idx = BM25Index().build(chunks)
    r = HybridRetriever(idx, chunks)
    query = "模板支撑立杆"
    bm = idx.search(query, top_k=3)
    only_bm = r.retrieve(query, top_k=3, vector_hits=[])
    both = r.retrieve(query, top_k=3, vector_hits=bm[:2])
    assert both[0].fused_score > only_bm[0].fused_score


def test_rule_boost_promotes_named_clause(chunks):
    """提问点名条款号时，该条款应被提到最前——合规审查的确定性要求。"""
    idx = BM25Index().build(chunks)
    r = HybridRetriever(idx, chunks)
    items = r.retrieve("模板支撑立杆怎么搭", top_k=5)
    boosted = rule_based_boost("第5.1.3条的要求是什么", items)
    assert boosted[0].chunk.clause_no == "第5.1.3条"


def test_extract_helpers():
    assert extract_clauses("第3.1.2 条的规定") == ["第3.1.2条"]
    assert extract_doc_names("《建筑施工基坑支护技术规程》JGJ120") == [
        "建筑施工基坑支护技术规程"
    ]


# ------------------------- 证据与拒答 -------------------------

def _mk_item(clause_no, mandatory=False, score=0.02, doc="建筑施工模板安全技术规范"):
    from app.rag.chunker import TextChunk

    return ScoredChunk(
        chunk=TextChunk(
            content=f"{clause_no} 严禁使用木支撑搭设。",
            doc_id="d",
            doc_name=doc,
            version="JGJ 80-2016",
            clause_no=clause_no,
            is_mandatory=mandatory,
        ),
        fused_score=score,
    )


def test_evidence_passes_with_strong_hit():
    """高分 + 多条依据 → 高置信度。"""
    ev = build_evidence(
        [
            _mk_item("第5.1.3条", mandatory=True, score=0.05),
            _mk_item("第4.2.1条", score=0.03),
        ]
    )
    assert ev.passed
    assert ev.confidence == Confidence.HIGH
    assert ev.citations[0].clause_no == "第5.1.3条"


def test_single_citation_caps_confidence():
    """只有一条依据时不应给高置信度——证据量不足，需人工复核。"""
    ev = build_evidence([_mk_item("第5.1.3条", mandatory=True, score=0.05)])
    assert ev.passed
    assert ev.confidence == Confidence.MEDIUM


def test_evidence_rejects_when_empty():
    """检索不到任何条款时必须拒答，而不是硬给结论。"""
    ev = build_evidence([])
    assert not ev.passed
    assert ev.reject_reason
    assert "无法给出合规判定" in render_no_evidence(ev)


def test_evidence_rejects_low_score():
    """分数过低说明知识库没覆盖，也必须拒答。"""
    ev = build_evidence([_mk_item("第1.1.1条", score=0.0001)])
    assert not ev.passed
    assert "置信度过低" in (ev.reject_reason or "")


def test_risk_levels_follow_evidence():
    strong = build_evidence([_mk_item("第5.1.3条", mandatory=True, score=0.05)])
    assert assess_risk(strong, keywords=["严禁", "木支撑"]).value == "high"

    weak = build_evidence([_mk_item("第1.1.1条", mandatory=False, score=0.02)])
    assert assess_risk(weak, keywords=[]).value == "compliant"


def test_rejected_evidence_maps_to_compliant():
    """拒答态不应被判为高风险——不能凭'查不到'就认定违规。"""
    ev = build_evidence([])
    assert assess_risk(ev, keywords=["严禁"]).value == "compliant"


def test_citation_dict_is_serializable(chunks):
    """引用必须能直接进 JSON 传给前端。"""
    import json

    idx = BM25Index().build(chunks)
    r = HybridRetriever(idx, chunks)
    ev = build_evidence(r.retrieve("立杆间距", top_k=3))
    json.dumps(ev.to_dict())  # 不抛异常即通过