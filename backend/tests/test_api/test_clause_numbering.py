"""编号统一与引用判定相关的测试。

锁住两个已修的真实 bug：
1. `citation_accuracy` 只认「第X条」格式，把官方附件编号判成无引用
2. `_normalize` 把「只有规范号、无项号」清成空串，导致映射既不生效也不报错

另守护映射表本身的完整性——映射目标必须真实存在，
写错的期望值会让 CI 把正确实现判成回归。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.clause_alias import CLAUSE_ALIAS, UNRESOLVED, is_mappable, map_clause
from eval.metrics import citation_accuracy

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
DATASET = BACKEND / "eval" / "datasets" / "regression_set.json"
PARSED = ROOT / ".cache" / "legal" / "parsed.json"


# ---------------------------------------------------------------------------
# 编号映射
# ---------------------------------------------------------------------------


def test_map_strips_regulation_prefix() -> None:
    """「建办质…31号 第三项」与「第三项」必须映射到同一条。"""
    assert map_clause("建办质〔2018〕31号 第三项") == map_clause("第三项")
    assert map_clause("建办质〔2018〕31号 第三项") == "附件2-模板工程及支撑体系（二）"


def test_map_bare_regulation_reference() -> None:
    """只有规范号、没有项号的笼统引用也必须能映射。

    这条踩过坑：归一化把「建办质〔2018〕31号」清成空串，
    结果既匹配不到映射表、又在集合判断里被当成空值，
    表现为「改不了也发现不了问题」。
    """
    assert map_clause("建办质〔2018〕31号") is not None


def test_jgj_is_not_mappable() -> None:
    """JGJ 系列属行业标准，不做映射（原文未获取，猜测会写错期望值）。"""
    for clause in (
        "JGJ 120-2012 第3.1.3条",
        "JGJ 80-2016 第4.2.1条",
        "JGJ 130-2011 第6.6.3条",
    ):
        assert is_mappable(clause) is False


def test_unknown_clause_returns_none() -> None:
    assert map_clause("完全不存在的一条") is None
    assert is_mappable("完全不存在的一条") is False


@pytest.mark.skipif(not PARSED.exists(), reason="需先运行 fetch_legal_text.py")
def test_alias_targets_exist_in_crawled_data() -> None:
    """映射目标必须在抓取数据中真实存在。

    写错的期望值比「判不出」更危险——它会让 CI 把正确实现判成回归。
    """
    entries = json.loads(PARSED.read_text(encoding="utf-8"))["entries"]
    valid = {e["clause_number"] for e in entries}

    missing = {k: v for k, v in CLAUSE_ALIAS.items() if v not in valid}
    assert not missing, (
        f"以下映射目标在抓取数据中不存在：{missing}\n"
        "请核对 clause_alias.py，或重新运行 fetch_legal_text.py"
    )


def test_unresolved_is_documented() -> None:
    """无法确认的映射必须显式记录原因，不能静默处理。"""
    assert UNRESOLVED, "应至少记录一条无法确认的映射"
    for clause, reason in UNRESOLVED.items():
        assert reason.strip(), f"{clause} 未写明无法映射的原因"
        # 明确标记为不映射
        assert clause not in CLAUSE_ALIAS


# ---------------------------------------------------------------------------
# 引用判定（曾误判官方编号为「无有效引用」）
# ---------------------------------------------------------------------------


class _R:
    """最小 EvalCaseResult 替身。"""

    def __init__(self, expect_clauses, citations):
        self.expect_clauses = expect_clauses
        self.citations = citations


@pytest.mark.parametrize(
    "citation",
    [
        "《危险性较大的分部分项工程安全管理规定》 住建部令第37号 第12条",
        "《危险性较大的分部分项工程范围》 建办质〔2018〕31号 附件1-基坑工程（一）",
        "建办质〔2018〕31号 附件2-暗挖工程",
        "附件2-起重吊装及起重机械安装拆卸工程（二）",
    ],
)
def test_citation_accuracy_accepts_both_numbering_schemes(citation: str) -> None:
    """两套编号体系都必须被认作有效引用。

    旧格式「第X条」曾正常，新格式「附件N-xxx」曾被判为无效，
    导致检索命中了正确条款、引用正确率却掉到 62.5%。
    """
    assert citation_accuracy(_R(["x"], [citation])) is True


def test_citation_accuracy_rejects_garbage() -> None:
    """不含任何条款标识的引用不算有效引用。"""
    assert citation_accuracy(_R(["x"], ["这是一段说明文字"])) is False


def test_citation_accuracy_skipped_without_expectation() -> None:
    assert citation_accuracy(_R([], ["第3条"])) is None


# ---------------------------------------------------------------------------
# 评测集本身
# ---------------------------------------------------------------------------


def test_dataset_expectations_should_not_contain_bare_regulation_ref() -> None:
    """评测集里不应再出现无项号的笼统引用——它永远匹配不上任何条款。"""
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    bare = [
        (c["case_id"], cl)
        for c in data["cases"]
        for cl in (c.get("expect_clauses") or [])
        if cl.strip() in ("建办质〔2018〕31号",)
    ]
    assert not bare, f"仍存在无项号引用：{bare}"