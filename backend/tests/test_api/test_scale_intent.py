"""规模档位（超规模 vs 普通危大）识别与加权的测试。

背景：建办质〔2018〕31号 把危大工程分两档——
附件1「危险性较大的分部分项工程」与附件2「超过一定规模的危大工程」。
两档阈值差很多（基坑 3m vs 5m、模板 5m vs 8m、吊装 10kN vs 100kN），
拿错档位会让审查结论反向。

而字符 n-gram 向量分不出这层语义（实测目标条款与错误条款分差仅 0.0001），
必须靠确定性规则补上。本文件把这些规则与踩过的坑固化成测试。

**当前状态**：代码已就位，但种子数据仍是旧编号体系（无「附件1/2」编号），
所以加权在现有数据上不会触发。待条款编号统一后自动生效。
"""
from __future__ import annotations

import pytest

from app.rag.chunker import TextChunk
from app.rag.retriever import (
    _chunk_is_over_scale,
    extract_scale_intent,
    rule_based_boost,
)


class _Scored:
    """最小可用的 ScoredChunk 替身，只保留加权所需的字段。"""

    def __init__(self, chunk: TextChunk, score: float = 0.01):
        self.chunk = chunk
        self.fused_score = score
        self.extra: dict = {}


def _chunk(clause: str, content: str, over: bool | None = None) -> TextChunk:
    extra = {"is_over_scale": over} if over is not None else {}
    return TextChunk(
        content=content,
        doc_id="doc",
        doc_name="建办质〔2018〕31号",
        clause_no=clause,
        extra=extra,
    )


# ---------------------------------------------------------------------------
# 规模意图识别
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "深基坑工程一般指开挖深度多少米以上？",
        "模板工程在什么情况下属于超过一定规模的危大工程？",
        "起重量300kN及以上的起重设备安装属于什么规模？",
        "脚手架需要专家论证吗",
        "这个要不要专家论证",
        "超过一定规模的危大工程有哪些",
    ],
)
def test_detect_over_scale_intent(query: str) -> None:
    assert extract_scale_intent(query) == "over"


@pytest.mark.parametrize(
    "query",
    [
        "这个工程属于危大工程吗",
        "开挖深度未超过5m的基坑算危大吗",
        "模板支撑不需要专家论证吗",
        "危大工程范围包括哪些",
    ],
)
def test_detect_normal_scale_intent(query: str) -> None:
    assert extract_scale_intent(query) == "normal"


@pytest.mark.parametrize(
    "query",
    [
        "食堂饭菜的营养搭配应该怎么设计",
        "悬挑式脚手架的悬挑梁锚固长度要求是多少？",
        "混凝土的配合比设计方法是什么",
    ],
)
def test_no_scale_intent(query: str) -> None:
    """未提规模时必须返回 None——此时不做任何倾向性加权。

    误判为 over 会把普通条款压下去，直接伤害召回。
    """
    assert extract_scale_intent(query) is None


def test_negation_beats_positive_marker() -> None:
    """否定语境必须优先于正向标记。

    这条最容易写错：「不需要专家论证」里含「专家论证」，
    若先判正向会把否定句当成「问超规模」，结论直接反向。
    """
    assert extract_scale_intent("不需要专家论证的工程") == "normal"
    assert extract_scale_intent("未超过一定规模的基坑") == "normal"
    assert extract_scale_intent("无需专家论证吗") == "normal"


# ---------------------------------------------------------------------------
# 条款档位判定
# ---------------------------------------------------------------------------


def test_over_scale_from_explicit_flag() -> None:
    assert _chunk_is_over_scale(_chunk("第三项", "…", over=True)) is True
    assert _chunk_is_over_scale(_chunk("第一项", "…", over=False)) is False


def test_over_scale_from_clause_number() -> None:
    """条款号带「附件2」即超规模，「附件1」即普通。"""
    assert _chunk_is_over_scale(_chunk("附件2-模板工程及支撑体系（二）", "…")) is True
    assert _chunk_is_over_scale(_chunk("附件1-基坑工程（一）", "…")) is False


def test_unknown_scale_returns_none() -> None:
    """无关条款（37号令程序性条款）必须返回 None，不得猜测。

    踩过的坑：曾用「8m 以上」从正文推断，把 37 号令第12条
    （专家论证程序，文中出现 50m 等数字）误判成超规模条款，
    加权后挤掉正确答案，Recall@5 从 95.83%掉到 83.33%。
    """
    assert _chunk_is_over_scale(_chunk("第12条", "专家人数不得少于5名，标高200m以上…")) is None
    assert _chunk_is_over_scale(_chunk("第11条", "专项施工方案应当包括下列内容…")) is None


# ---------------------------------------------------------------------------
# 加权行为
# ---------------------------------------------------------------------------


def test_over_scale_query_promotes_over_scale_clause() -> None:
    items = [
        _Scored(_chunk("附件1-模板工程及支撑体系（二）", "搭设高度5m及以上", over=False)),
        _Scored(_chunk("附件2-模板工程及支撑体系（二）", "搭设高度8m及以上", over=True)),
    ]
    out = rule_based_boost("什么情况下属于超过一定规模的危大工程", items)
    assert out[0].chunk.clause_no.startswith("附件2"), (
        f"超规模查询应把附件2条款排前，实际首位是 {out[0].chunk.clause_no}"
    )


def test_normal_query_promotes_normal_clause() -> None:
    items = [
        _Scored(_chunk("附件2-基坑工程", "开挖深度超过5m", over=True)),
        _Scored(_chunk("附件1-基坑工程（一）", "开挖深度超过3m", over=False)),
    ]
    out = rule_based_boost("基坑工程属于危大工程吗", items)
    assert out[0].chunk.clause_no.startswith("附件1")


def test_boost_never_demotes() -> None:
    """加权只升不降——这是被评测数据逼出来的设计。

    初版对错档位条款给 -1.0 降权，实测 Recall@5 从 95.83% 掉到 91.67%、
    MRR 从 80.21% 掉到 66.81%。很多查询同时需要两档条款，
    一压就整条掉出 Top5。

    漏一条次要条款只是多问一句；把唯一正确条款压出去会让 Agent
    拿错阈值直接给出错误结论——代价不对称。
    """
    items = [
        _Scored(_chunk("附件2-模板（二）", "搭设高度8m及以上", over=True)),
        _Scored(_chunk("附件1-模板（二）", "搭设高度5m及以上", over=False)),
    ]
    baseline = [i.fused_score for i in items]
    out = rule_based_boost("什么情况下属于超过一定规模的危大工程", items)
    after = {i.chunk.clause_no: i.fused_score for i in out}

    assert after["附件1-模板（二）"] >= baseline[1], "错档位条款被降权了，违反只升不降约定"


def test_no_scale_query_leaves_scores_untouched() -> None:
    """未提规模时，加权逻辑不应改变任何条目的分数。"""
    items = [
        _Scored(_chunk("附件1-模板（二）", "搭设高度5m及以上", over=False), 0.02),
        _Scored(_chunk("附件2-模板（二）", "搭设高度8m及以上", over=True), 0.01),
    ]
    out = rule_based_boost("模板支撑的搭设要求是什么", items)
    scores = {i.chunk.clause_no: round(i.fused_score, 6) for i in out}
    assert scores["附件1-模板（二）"] == 0.02
    assert scores["附件2-模板（二）"] == 0.01

# ---------------------------------------------------------------------------
# BM25 索引文本（短条款召回修复）
# ---------------------------------------------------------------------------


def test_index_text_includes_clause_and_category() -> None:
    """条款号与领域标签必须进 BM25 索引。

    实测踩过的坑：`附件2-暗挖工程` 正文只有 26 字，查询里的高频词
    「专家论证」在 37 号令多处出现，BM25 被其主导，该条款**根本不在
    Top30 候选池内**——而规则加权只能重排池内文档，权重再高也救不回来。
    条款号里的「暗挖」正是查询中最强的领域词，必须参与检索。
    """
    from app.rag.retriever import _index_text

    chunk = TextChunk(
        content="采用矿山法、盾构法、顶管法施工的隧道、洞室工程。",
        doc_id="d",
        doc_name="建办质〔2018〕31号",
        clause_no="附件2-暗挖工程",
        extra={"category": "underground", "is_over_scale": True},
    )
    text = _index_text(chunk)

    assert "暗挖" in text, "条款号中的领域词必须进索引"
    assert "underground" in text, "category 标签必须进索引"
    assert "采用矿山法" in text, "正文必须保留"


def test_index_text_handles_missing_optional_fields() -> None:
    """缺条款号/category 时不能报错。"""
    from app.rag.retriever import _index_text

    chunk = TextChunk(content="正文内容", doc_id="d", doc_name="法规")
    assert _index_text(chunk) == "正文内容"


def test_short_clause_is_retrievable_by_domain_word() -> None:
    """短条款应能被「领域 + 档位」的查询召回。"""
    from app.rag.chunker import TextChunk
    from app.rag.retriever import BM25Index, HybridRetriever

    chunks = [
        TextChunk(
            content="采用矿山法、盾构法、顶管法施工的隧道、洞室工程。",
            doc_id="a",
            doc_name="建办质〔2018〕31号",
            clause_no="附件2-暗挖工程",
            extra={"category": "underground", "is_over_scale": True},
        ),
        TextChunk(
            content="施工单位应当组织召开专家论证会对专项施工方案进行论证。"
            * 4,
            doc_id="b",
            doc_name="住建部令第37号",
            clause_no="第12条",
            extra={"category": "other"},
        ),
    ]
    index = BM25Index().build(chunks)
    r = HybridRetriever(index, chunks)
    hits = r.retrieve("暗挖工程需要专家论证的情形有哪些？", top_k=10)
    clauses = [h.chunk.clause_no for h in hits]
    assert "附件2-暗挖工程" in clauses, (
        f"短条款未被召回，候选池={clauses}"
    )
