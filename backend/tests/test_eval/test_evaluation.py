"""评测框架的 pytest 集成。

对应 JD「建立评测→反馈→调优→再评测的持续迭代闭环」（腾讯 JD 原文）。

两种模式：
- `pytest test_eval/ -m eval` 跑全量评测，人工查看报告
- `pytest test_eval/ -m regression` 跑回归检测，指标下降超阈值直接让 CI 失败
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.metrics import (
    assert_no_regression,
    citation_accuracy,
    compare,
    hit_at_k,
    recall_at_k,
    reciprocal_rank,
    refusal_correctness,
)
from eval.runner import (
    REPORTS_DIR,
    RealRetriever,
    load_dataset,
    run_eval,
)

pytestmark = pytest.mark.eval


@pytest.fixture(scope="module")
def dataset():
    return load_dataset("regression_set")


@pytest.fixture(scope="module")
def report(dataset):
    from app.data.seed import load_seed_chunks

    knowledge = load_seed_chunks()
    if not knowledge:
        pytest.skip("种子法规数据缺失")
    return run_eval(dataset, RealRetriever(knowledge))


# ------------------------------------------------------------ 指标计算单元测试


def test_normalize_clause_handles_all_formats():
    """条款号归一化必须兼容多种书写形式，否则评测结果不可信。"""
    from eval.metrics import _normalize_clause

    # 同一条款的不同写法应归一到同一值
    assert _normalize_clause("第4.2.1条") == _normalize_clause("JGJ 80-2016 第4.2.1条")
    assert _normalize_clause("第4.2.1条") == _normalize_clause("4.2.1")
    # 章/节 与 条 同级
    assert _normalize_clause("JGJ 120-2012 第8章") == "8"
    # 中文数字（建办质31号用序号分项）
    assert _normalize_clause("第三项") == "3"
    assert _normalize_clause("第七项") == "7"
    assert _normalize_clause("第十项") == "10"


def test_clause_normalization_caught_real_bug():
    """回归用例：早期版本直接整体替换'第'和'条'，
    导致 'JGJ 80-2016 第4.2.1条' → 'JGJ80-20164.2.1' 永远匹配不上，
    Recall@5 被低估到 37%。此用例防止该bug 复现。
    """
    from eval.metrics import EvalCaseResult, RetrievedItem

    r = EvalCaseResult(
        case_id="t",
        category="formwork",
        difficulty="direct",
        should_refuse=False,
        retrieved=[RetrievedItem(clause_no="第4.2.1条", content="x")],
    )
    r.expect_clauses = ["JGJ 80-2016 第4.2.1条"]
    assert recall_at_k(r) == 1.0


# ------------------------------------------------------------ 端到端指标断言


@pytest.mark.regression
def test_recall_at_5_meets_target(report):
    m = report.metrics["recall_at_5"]
    assert m >= 0.95, f"Recall@5 {m:.2%} 低于目标 95%"


@pytest.mark.regression
def test_hit_rate_meets_target(report):
    m = report.metrics["hit_rate"]
    assert m >= 0.98, f"命中率 {m:.2%} 低于目标 98%"


@pytest.mark.regression
def test_citation_accuracy_is_perfect(report):
    """引用正确率必须 100%——合规审查的底线，不容妥协。"""
    m = report.metrics.get("citation_accuracy")
    assert m == 1.0, f"引用正确率 {m:.2%}，低于 100%"


@pytest.mark.regression
def test_refusal_accuracy_meets_target(report):
    m = report.metrics["refusal_accuracy"]
    assert m >= 0.95, f"拒答准确率 {m:.2%} 低于目标 95%"


@pytest.mark.regression
def test_false_refusal_rate_acceptable(report):
    m = report.metrics["false_refusal_rate"]
    assert m <= 0.03, f"误拒率 {m:.2%} 高于上限 3%"


def test_adversarial_cases_all_refused(dataset, report):
    """对抗样本必须全部拒答——这是本项目最核心的差异化能力。"""
    adv = [c for c in dataset["cases"] if c.get("should_refuse")]
    assert adv, "评测集必须包含对抗样本"

    failed = [c for c in adv if not _was_refused(c["case_id"], report)]
    assert not failed, f"对抗样本未拒答: {[c['case_id'] for c in failed]}"


def _was_refused(case_id: str, report) -> bool:
    """从评测报告中反查某用例是否被拒答。"""
    for f in report.failures:
        if f["case_id"] == case_id and "拒答" not in f["reason"]:
            return False
    return True


@pytest.mark.regression
def test_no_regression_against_baseline(report):
    """与基线对比：任何核心指标下降超 3% 即失败。"""
    baseline_path = REPORTS_DIR / "baseline.json"
    if not baseline_path.exists():
        pytest.skip("尚无基线报告，首次运行时跳过")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))["metrics"]
    assert_no_regression(baseline, report.metrics)


def test_report_generated(report):
    """报告文件应包含完整指标，便于人工查看与面试展示。"""
    assert report.total == 27
    assert "Recall@5" in report.to_markdown()
    assert len(report.by_category) >= 7
    assert len(report.by_difficulty) >= 3


def test_all_categories_covered(dataset):
    """七大类危大工程必须全部有评测样本，否则指标会被高频类别主导。"""
    categories = {c["category"] for c in dataset["cases"]}
    expected = {
        "foundation_pit",
        "formwork",
        "lifting",
        "scaffold",
        "demolition",
        "underground",
        "other",
    }
    assert expected.issubset(categories), f"缺少类别: {expected - categories}"


def test_dataset_has_difficulty_levels(dataset):
    """四种难度必须齐备：直给/语义/跨条款/对抗。"""
    difficulties = {c["difficulty"] for c in dataset["cases"]}
    assert {"direct", "semantic", "cross_ref", "adversarial"}.issubset(difficulties)