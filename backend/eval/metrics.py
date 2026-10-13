"""评测指标计算。

对应 JD 频次 34%（腾讯「可量化的 Agent 效果评估体系」、
某半导体岗「Faithfulness、Answer Relevancy、Context Precision」）。

设计原则：**指标必须能反映真实的检索/生成质量，而不是自说自话。**
所以：
- 检索指标只看排序，不看 LLM 打分（LLM 打分不可复现）
- 生成指标中，引用正确性用字符串精确匹配而非模型判定
- 每个指标都有明确的计算口径，便于第三方复现
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 数据结构


@dataclass
class RetrievedItem:
    """单条检索结果。"""

    clause_no: str | None
    content: str
    regulation_code: str | None = None
    score: float = 0.0


@dataclass
class EvalCaseResult:
    """单条用例的评测结果。"""

    case_id: str
    category: str
    difficulty: str
    should_refuse: bool

    retrieved: list[RetrievedItem] = field(default_factory=list)
    answer: str = ""
    refused: bool = False
    refuse_reason: str | None = None
    citations: list[str] = field(default_factory=list)

    # 计算结果
    recall_at_k: float = 0.0
    hit: bool = False
    reciprocal_rank: float = 0.0
    citation_correct: bool | None = None
    correct_refusal: bool | None = None
    error: str | None = None


# ---------------------------------------------------------------- 检索指标


def _normalize_clause(s: str) -> str:
    """从条款号字符串中抽取可比较的条款标识。

    评测集里的期望值可能写成「JGJ 80-2016 第4.2.1条」「第3条」「第8章」
    等多种形式，而检索结果只有「第4.2.1条」。若直接整体归一化，
    前者会变成 "JGJ80-20164.2.1" 永远匹配不上后者。
    因此分三级抽取：带第X条 → 带第X章/节 → 裸条款号。
    """
    s = re.sub(r"\s+", "", s)

    # 「第4.2.1条」「第八项」中的阿拉伯数字形式
    m = re.search(r"第([\d.]+)[条项]", s)
    if m:
        return m.group(1).strip(".")

    # 「第8章」「第4节」——章/节 与 条 同级可比较
    m = re.search(r"第([\d.]+)[章节]", s)
    if m:
        return m.group(1).strip(".")

    # 「第三项」这类中文数字——建办质〔2018〕31号用序号分项，需转阿拉伯数字
    m = re.search(r"第([一二三四五六七八九十]+)[条项章节]", s)
    if m:
        return str(_cn_to_int(m.group(1)))

    # 已是裸条款号
    if re.fullmatch(r"[\d.]+", s):
        return s.strip(".")

    # 无条款信息，退化为整串比较
    return s


_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_to_int(s: str) -> int:
    """中文数字转阿拉伯数字，支持「十」「十一」「二十」等写法。"""
    if not s:
        return 0
    if "十" not in s:
        return sum(_CN_DIGITS.get(c, 0) for c in s)
    left, _, right = s.partition("十")
    tens = _CN_DIGITS.get(left, 1) if left else 1
    ones = _CN_DIGITS.get(right, 0) if right else 0
    return tens * 10 + ones


def recall_at_k(result: EvalCaseResult, k: int = 5) -> float:
    """Recall@k：Top-k 中命中的期望条款占比。

    多条款期望时（如同时问危大与超规模），按命中率而非全对全错计算。
    """
    if not result.expect_clauses:
        return 1.0 if not result.should_refuse else 0.0

    got = {_normalize_clause(i.clause_no) for i in result.retrieved[:k] if i.clause_no}
    expect = {_normalize_clause(c) for c in result.expect_clauses}

    if not expect:
        return 0.0
    return len(got & expect) / len(expect)


def hit_at_k(result: EvalCaseResult, k: int = 5) -> bool:
    """命中：Top-k 中存在任一期望条款。"""
    if not result.expect_clauses:
        return not result.retrieved
    return recall_at_k(result, k) > 0


def reciprocal_rank(result: EvalCaseResult) -> float:
    """MRR：期望条款首次出现排名的倒数。

    只有 0 和 1/k 两种取值是正常的——多条期望条款时取最靠前那条。
    """
    if not result.expect_clauses:
        return 0.0
    expect = {_normalize_clause(c) for c in result.expect_clauses}

    for rank, item in enumerate(result.retrieved, start=1):
        if item.clause_no and _normalize_clause(item.clause_no) in expect:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------- 生成指标


def citation_accuracy(result: EvalCaseResult) -> bool | None:
    """引用正确性：期望答案有引用要求时，判断产出是否带引用。

    只判断「有没有引用」和「引用是否含可定位的条款标识」，
    不判断引用内容语义正确——那需要 LLM-as-judge，不够稳定。

    **条款标识要同时支持两套编号体系**：
    - 旧格式「住建部令第37号 第12条」→ 匹配 `第[\\d.]+条`
    - 新格式「建办质〔2018〕31号 附件2-模板工程及支撑体系（二）」
      → 匹配「附件N-…」（官方附件编号）

    只认旧格式会导致一个隐蔽后果：知识库换成官方编号后，
    检索其实命中了正确条款，但指标判成「无有效引用」，
    引用正确率从 100% 掉到 62.5%——**指标本身成了 bug**。
    """
    if not result.expect_clauses:
        return None
    if not result.citations:
        return False

    for c in result.citations:
        # 标准编号条款：JGJ 80-2016 第4.2.1条
        if re.search(r"第[\d.]+条", c):
            return True
        # 规章正文条款：第37号 第3条
        if re.search(r"[A-Z]{2,}\s*\d+", c):
            return True
        # 官方附件编号：附件1-基坑工程（一）/ 附件2-暗挖工程
        if re.search(r"附件\s*\d+\s*[-－—]", c):
            return True
        # 独立写法的附件编号（无规范号前缀时）
        if re.search(r"^附件\s*\d+", c.strip()):
            return True
    return False


def refusal_correctness(result: EvalCaseResult) -> bool | None:
    """拒答正确性：对抗样本期望拒答，正常样本期望不拒答。"""
    if result.should_refuse:
        return result.refused
    # 非对抗样本若拒答，算作错误（该答没答）
    return not result.refused


# ---------------------------------------------------------------- 汇总


@dataclass
class EvalReport:
    """评测汇总报告。"""

    total: int
    metrics: dict[str, float]
    by_category: dict[str, dict[str, float]]
    by_difficulty: dict[str, dict[str, float]]
    failures: list[dict]
    duration_sec: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "metrics": self.metrics,
            "by_category": self.by_category,
            "by_difficulty": self.by_difficulty,
            "failures": self.failures,
            "duration_sec": round(self.duration_sec, 2),
        }

    def to_markdown(self) -> str:
        m = self.metrics
        lines = [
            "# 评测报告",
            "",
            f"样本数：{self.total}　耗时：{self.duration_sec:.1f}s",
            "",
            "## 核心指标",
            "",
            "| 指标 | 数值 | 目标 | 达标 |",
            "|---|---|---|---|",
        ]
        targets = [
            ("Recall@5", m.get("recall_at_5"), 0.95),
            ("命中率 Hit@5", m.get("hit_rate"), 0.98),
            ("MRR", m.get("mrr"), 0.90),
            ("引用正确率", m.get("citation_accuracy"), 1.00),
            ("拒答准确率", m.get("refusal_accuracy"), 0.95),
            ("误拒率", m.get("false_refusal_rate"), 0.03),
        ]
        for name, val, target in targets:
            if val is None:
                continue
            # 误拒率是越低越好，比较方向相反
            ok = val <= target if name == "误拒率" else val >= target
            lines.append(
                f"| {name} | {val:.2%} | {'≤' if name == '误拒率' else '≥'}{target:.0%} | "
                f"{'✅' if ok else '❌'} |"
            )

        lines += ["", "## 分类别表现", ""]
        lines += ["| 类别 | 样本 | Recall@5 | 拒答正确 |", "|---|---|---|---|"]
        for cat, v in self.by_category.items():
            lines.append(
                f"| {cat} | {v['count']:.0f} | {v['recall_at_5']:.2%} | "
                f"{v.get('refusal_accuracy', 0):.2%} |"
            )

        lines += ["", "## 分类难度表现", ""]
        lines += ["| 难度 | 样本 | Recall@5 | 命中率 |", "|---|---|---|---|"]
        for d, v in self.by_difficulty.items():
            lines.append(
                f"| {d} | {v['count']:.0f} | {v['recall_at_5']:.2%} | {v['hit_rate']:.2%} |"
            )

        if self.failures:
            lines += ["", "## 失败用例（Bad Case）", ""]
            lines += ["| 用例 | 问题 | 类别 | 原因 |", "|---|---|---|---|"]
            for f in self.failures[:20]:
                lines.append(
                    f"| {f['case_id']} | {f.get('question', '')[:30]} | "
                    f"{f['category']} | {f.get('reason', '')} |"
                )
        return "\n".join(lines)


def aggregate(results: list[EvalCaseResult], duration: float = 0.0) -> EvalReport:
    """汇总所有用例结果。

    拒答样本不计入检索指标——它们本来就不该召回到东西。
    否则会把拒答机制的成功误算成检索失败。
    """
    valid = [r for r in results if not r.should_refuse]
    adversarial = [r for r in results if r.should_refuse]

    metrics: dict[str, float] = {}
    if valid:
        metrics["recall_at_5"] = sum(r.recall_at_k for r in valid) / len(valid)
        metrics["hit_rate"] = sum(1 for r in valid if r.hit) / len(valid)
        metrics["mrr"] = sum(r.reciprocal_rank for r in valid) / len(valid)
        cit = [r.citation_correct for r in valid if r.citation_correct is not None]
        if cit:
            metrics["citation_accuracy"] = sum(cit) / len(cit)

    # 拒答指标：对抗样本要拒 + 正常样本别乱拒
    refuse_correct = [r.correct_refusal for r in results if r.correct_refusal is not None]
    if refuse_correct:
        metrics["refusal_accuracy"] = sum(refuse_correct) / len(refuse_correct)
    false_refusals = sum(1 for r in valid if r.refused)
    if valid:
        metrics["false_refusal_rate"] = false_refusals / len(valid)

    def group(key: str) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, list]] = {}
        for r in valid:
            out.setdefault(getattr(r, key), {"recall": [], "hit": [], "refuse": []})
            out[getattr(r, key)]["recall"].append(r.recall_at_k)
            out[getattr(r, key)]["hit"].append(1.0 if r.hit else 0.0)
            out[getattr(r, key)]["refuse"].append(r.correct_refusal or 0.0)
        return {
            k: {
                "count": len(v["recall"]),
                "recall_at_5": sum(v["recall"]) / len(v["recall"]),
                "hit_rate": sum(v["hit"]) / len(v["hit"]),
                "refusal_accuracy": sum(v["refuse"]) / len(v["refuse"]),
            }
            for k, v in out.items()
        }

    failures = []
    for r in results:
        reason = ""
        if r.error:
            reason = f"执行错误: {r.error}"
        elif r.should_refuse and not r.refused:
            reason = "应拒答但给出了答案"
        elif r.should_refuse and r.refused:
            continue  # 拒答成功，不是失败
        elif r.correct_refusal is False:
            reason = "误拒：正常问题被拒答"
        elif not r.hit:
            reason = f"检索未命中（Recall@5={r.recall_at_k:.0%}）"
        elif r.citation_correct is False:
            reason = "缺少有效引用"
        if reason:
            failures.append(
                {
                    "case_id": r.case_id,
                    "category": r.category,
                    "difficulty": r.difficulty,
                    "reason": reason,
                }
            )

    return EvalReport(
        total=len(results),
        metrics=metrics,
        by_category=group("category"),
        by_difficulty=group("difficulty"),
        failures=failures,
        duration_sec=duration,
    )


# ---------------------------------------------------------------- 基线对比


def compare(baseline: dict, current: dict) -> dict:
    """与基线对比，识别回退。

    阈值 3%：跌破即视为回退。宁可误报也不要漏掉检索质量下降——
    后者在用户投诉前不会被发现。
    """
    diff = {}
    for k, v in current.items():
        if k not in baseline:
            continue
        b = baseline[k]
        delta = v - b
        # 误拒率越低越好，方向相反
        better = delta <= 0.003 if k == "false_refusal_rate" else delta >= -0.03
        diff[k] = {
            "baseline": round(b, 4),
            "current": round(v, 4),
            "delta": round(delta, 4),
            "status": "improve" if delta > 0.003 else ("regress" if delta < -0.03 else "same"),
            "pass": better,
        }
    return diff


def assert_no_regression(baseline: dict, current: dict, threshold: float = 0.03) -> None:
    """CI 中调用：任何核心指标下降超阈值即抛异常，让流水线失败。"""
    d = compare(baseline, current)
    regressed = [k for k, v in d.items() if v["status"] == "regress"]
    if regressed:
        detail = "; ".join(f"{k} {d[k]['baseline']:.2%}→{d[k]['current']:.2%}" for k in regressed)
        raise AssertionError(f"评测指标回退（阈值 {threshold:.0%}）：{detail}")