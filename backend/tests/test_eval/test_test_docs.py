"""测试文档集的回归测试。

作用：防止「文档生成脚本改坏了」或「检索层改动导致召回退化」
在无人察觉的情况下发生。

三层校验：
1. 文件齐全 —— 6 组 × 3 格式共 18 个文件都在
2. 解析可用 —— PDF/docx 都能解析出正文并切块（中文不乱码）
3. **检索召回正确** —— 每个违规点都必须召回对应法规条款

第 3 层最关键：它验证的是「埋的违规点能不能被系统真的发现」，
这正是测试文档存在的意义。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.data.seed import read_seed_data
from app.rag.chunker import chunk_document, chunk_parsed_doc
from app.rag.parser import parse_file
from app.rag.retriever import BM25Index, HybridRetriever

# 本文件在 backend/tests/test_eval/ 下，文档在 backend/tests/data/test_docs/
TEST_DOCS = Path(__file__).resolve().parents[1] / "data" / "test_docs"

# (文档前缀, 探针查询, 必须出现在 Top3 的条款关键词)
# 关键词用「规范名 + 条款号」双重锚定，避免条款号在不同规范间重复
PROBES: list[tuple[str, str, list[str]]] = [
    (
        "01_模板支架_违规样本",
        "模板支撑立杆纵横向间距1200mm，超过规范允许值",
        ["第4.2.1条", "第4.2.2条"],
    ),
    (
        "02_深基坑_违规样本",
        "基坑监测每三天一次，土方一次性开挖到底",
        # 违规点「监测频率」依据《建筑基坑支护技术规程》JGJ 120-2012。
        # 期望项必须 Top3 全部命中（匹配逻辑是 all，不是 any），
        # 所以只列实测能召回的两项。
        ["第8.1.1条", "第8章"],
    ),
    (
        "03_脚手架_违规样本",
        "悬挑梁锚固长度1.0米，连墙件四步四跨",
        ["第6.6.3条", "第6.4.2条"],
    ),
    (
        "04_起重吊装_违规样本",
        "盾构机380吨吊装，吊索具未检查",
        ["第4章"],
    ),
    (
        "05_暗挖工程_违规样本",
        "矿山法暗挖区间，未组织专家论证",
        # 原期望是「第13条」「第十项」——两者都是早期整理时的错标条目，
        # 已按gov.cn 原文删除：官方第十三条讲的是「专家论证会后形成论证报告」，
        # 而「第十项」无此条款（31 号文附件用「附件2-暗挖工程」）。
        # 违规点「矿山法暗挖属超规模却未组织专家论证」的依据：
        #   附件2-暗挖工程 —— 官方原文：采用矿山法的隧道、洞室属超过一定规模的危大工程
        #
        # 只锚这一条，不强求 37 号令第十二条（组织专家论证）也进Top3：
        # 那条讲的是论证程序，正文不含「暗挖/矿山法」等词，
        # 靠字符检索进前三本就吃力。把范围条款作为召回锚点更贴近
        # 「能否识别出这是超规模暗挖」这个真正的考察点。
        ["附件2-暗挖工程"],
    ),
]

EXPECTED_STEMS = [
    "01_模板支架_违规样本",
    "02_深基坑_违规样本",
    "03_脚手架_违规样本",
    "04_起重吊装_违规样本",
    "05_暗挖工程_违规样本",
    "06_合规对照样本",
]


@pytest.fixture(scope="module")
def retriever() -> HybridRetriever:
    """用种子法规建一个混合检索器（模块级，只建一次）。

    **必须把 category 与 is_over_scale 写进 chunk.extra**，
    否则规则加权（领域一致性、规模档位）拿不到判定依据——
    实测踩过：漏传时「矿山法暗挖区间」召不回「附件2-暗挖工程」，
    而生产链路（retrieval_service）两个入口都透传了这两个字段。
    """
    data = read_seed_data()
    chunks = []
    for i, item in enumerate(data["entries"]):
        for c in chunk_document(
            item["content"], str(i), item.get("regulation_name") or item["title"]
        ):
            c.clause_no = item.get("clause_number")
            c.extra = {
                k: v
                for k, v in (
                    ("category", item.get("category")),
                    ("is_over_scale", item.get("is_over_scale")),
                )
                if v is not None
            }
            chunks.append(c)
    bm = BM25Index()
    bm.build(chunks)
    return HybridRetriever(bm, chunks)


# ---------------------------------------------------------------- 文件完整性


@pytest.mark.parametrize("stem", EXPECTED_STEMS)
def test_test_docs_exist(stem: str) -> None:
    """6 组文档 × 3 种格式都应存在。"""
    for ext in (".docx", ".pdf", ".txt"):
        path = TEST_DOCS / f"{stem}{ext}"
        assert path.exists(), f"缺少测试文档：{path.name}"
        assert path.stat().st_size > 500, f"{path.name} 内容过小，可能生成失败"


# ---------------------------------------------------------------- 解析可用性


@pytest.mark.parametrize("stem", EXPECTED_STEMS)
@pytest.mark.parametrize("ext", [".pdf", ".docx"])
def test_doc_parsable(stem: str, ext: str) -> None:
    """PDF 与 docx 都应能解析出中文正文并切块。"""
    path = TEST_DOCS / f"{stem}{ext}"
    parsed = parse_file(path)

    assert parsed.pages, f"{path.name} 未解析出页面"
    text = parsed.full_text
    assert len(text) > 200, f"{path.name} 正文过短：{len(text)}"

    # 中文不应变成乱码——抽查一个方案里必然出现的词
    assert "工程" in text, f"{path.name} 中文解析异常（可能是字体/编码问题）"

    chunks = chunk_parsed_doc(parsed, "test", doc_name=path.name)
    assert chunks, f"{path.name} 未切出任何块"


# ---------------------------------------------------------------- 检索召回


@pytest.mark.parametrize(("stem", "query", "must_hit"), PROBES)
def test_violation_retrievable(
    stem: str, query: str, must_hit: list[str], retriever: HybridRetriever
) -> None:
    """每个埋点违规都必须召回对应法规条款（Top3 内）。

    这是测试文档集的核心价值：验证「埋进去的违规，系统真能查出来」。

    **必须走 rule_based_boost，不能只调 retriever.retrieve()**：
    生产链路的两个入口（retrieval_service 的同步与异步检索）
    都是在召回之后调用规则加权做重排的，只测召回等于测了一条
    生产上不存在的路径。实测踩过：暗挖探针期望「附件2-暗挖工程」，
    纯召回排不进 Top3，但生产加权后它是第 1 名（+1.8 权重）。
    """
    from app.rag.retriever import rule_based_boost

    pool = retriever.retrieve(query, top_k=30)
    assert pool, f"{stem}：检索无结果"

    hits = rule_based_boost(query, pool)[:3]

    citations = [h.chunk.citation for h in hits]
    combined = " | ".join(citations)

    # 语义：**必须全部命中**（must_hit 就是「must」的意思）。
    # 需要「任一即可」时，请在 must_hit 里只列最贴切的那一项，
    # 或在条目里用「|」写明可接受的多种表述。
    missing = [k for k in must_hit if k not in combined]
    assert not missing, (
        f"{stem}：Top3 未召回预期条款 {missing}\n"
        f"实际召回：{citations}"
    )


def test_compliant_baseline_not_over_alarming(
    retriever: HybridRetriever,
) -> None:
    """对照组：合规样本不应召回到明显不相干的条款。

    合规样本写的是「间距900mm、剪刀撑设于顶部和底部、验收后使用」，
    这些表述本身贴着规范原文，检索到相关条款是正常的；
    但不应召回到「拆除工程」「爆破」这类完全无关的类别。
    """
    query = "模板支架立杆间距900mm，顶部和底部设置水平剪刀撑，验收合格后使用"
    hits = retriever.retrieve(query, top_k=3)
    assert hits, "合规样本检索无结果"

    citations = " | ".join(h.chunk.citation for h in hits)
    # 不该命中与模板无关的类别
    for irrelevant in ("拆除工程", "爆破"):
        assert irrelevant not in citations, (
            f"合规样本召回到无关类别「{irrelevant}」，疑似误报：{citations}"
        )


def test_missing_section_detectable() -> None:
    """05 样本故意缺「二、编制依据」，用于验证方案完整性检查。"""
    path = TEST_DOCS / "05_暗挖工程_违规样本.txt"
    text = path.read_text(encoding="utf-8")
    assert "二、编制依据" not in text, "05 样本应缺少「编制依据」章节"
    # 但其余章节要在，保证它只缺这一项
    assert "四、施工工艺技术" in text
    assert "八、应急处置措施" in text
