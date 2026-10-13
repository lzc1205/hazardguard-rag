"""评测流水线。

对应 JD「实现自动化评测流水线」（某半导体岗原文）。

关键设计：**评测必须走真实检索链路**，不能mock 检索结果——
否则测的是mock 的表现，不是系统的表现，指标毫无意义。
但 LLM 调用支持 mock 模式，避免跑一次评测烧几十元。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.metrics import (
    EvalCaseResult,
    EvalReport,
    RetrievedItem,
    aggregate,
    assert_no_regression,
    citation_accuracy,
    compare,
    hit_at_k,
    recall_at_k,
    reciprocal_rank,
    refusal_correctness,
)

DATASETS_DIR = Path(__file__).parent / "datasets"
REPORTS_DIR = Path(__file__).parent / "reports"


def load_dataset(name: str = "regression_set") -> dict:
    path = DATASETS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"评测集不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


class MockRetriever:
    """Mock 检索器——仅用于验证评测框架自身是否正确，不用于产出真实指标。

    存在的意义：没有真实知识库时也能确认「指标计算逻辑没写错」，
    否则框架 bug 会被误当成系统性能问题。
    """

    def __init__(self, cases: list[dict]):
        self._by_clause: dict[str, list[str]] = {}
        for c in cases:
            for clause in c.get("expect_clauses", []):
                self._by_clause.setdefault(clause, []).append(c["case_id"])

    def search(self, question: str, top_k: int = 5) -> list[RetrievedItem]:
        hits = []
        for clause in self._by_clause:
            if clause in question or question[:4] in clause:
                hits.append(
                    RetrievedItem(clause_no=clause, content=f"[mock] {clause}", score=0.9)
                )
        return hits[:top_k]


class RealRetriever:
    """真实检索器——接app.rag 的BM25 + 规则加权。

    必须透传 category：领域一致性加权依赖它来判断条款属于哪个危大类别。
    """

    def __init__(self, knowledge: list[tuple]):
        """knowledge: [(clause_no, regulation_code, content[, category[, is_over_scale]]), ...]

        必须透传 category 与 is_over_scale：
        前者供领域一致性加权，后者供「超规模 vs 普通危大」档位加权——
        两者都是字符 n-gram 无法自行分辨、必须靠结构化标记补上的信号。
        """
        from app.rag.chunker import TextChunk
        from app.rag.retriever import BM25Index, HybridRetriever, rule_based_boost

        self.chunks = [
            TextChunk(
                content=content,
                doc_id=code or "unknown",
                doc_name=code or "法规",
                version=code,
                clause_no=clause,
                extra={
                    k: v
                    for k, v in (
                        ("category", rest[0] if len(rest) > 0 else None),
                        ("is_over_scale", rest[1] if len(rest) > 1 else None),
                    )
                    if v is not None
                },
            )
            for clause, code, content, *rest in knowledge
        ]
        self.index = BM25Index().build(self.chunks)
        self.retriever = HybridRetriever(self.index, self.chunks)
        self._boost = rule_based_boost

    def search(self, question: str, top_k: int = 5) -> list[RetrievedItem]:
        """先取超额候选，再加权重排，最后截断。

        关键：规则加权只能重排「已进入候选池」的文档。
        若retrieve 只取 top_k，目标条款排在第 7 名时会被直接丢弃，
        加权再高也救不回来。因此召回阶段必须超额取数
        （实测：暗挖工程那条条款在 top5 外、top30 内排第 1）。
        """
        pool_size = max(top_k * 6, 30)
        items = self.retriever.retrieve(question, top_k=pool_size)
        items = self._boost(question, items)
        return [
            RetrievedItem(
                clause_no=i.chunk.clause_no,
                content=i.chunk.content,
                regulation_code=i.chunk.version,
                score=i.final_score,
            )
            for i in items[:top_k]
        ]


class MockLLM:
    """Mock LLM——按评测集标注生成答案，用于验证评测框架。

    真实评测时替换为 app.llm 的实现；此处不调用任何模型。
    """

    def __init__(self, cases: list[dict]):
        self._answers = {c["case_id"]: c for c in cases}

    def answer(self, case_id: str, question: str, retrieved: list[RetrievedItem]):
        spec = self._answers.get(case_id, {})
        if spec.get("should_refuse"):
            return "", True, "知识库中未检索到相关条款", []
        citations = [
            f"《{i.regulation_code or '法规'}》{i.clause_no}" for i in retrieved[:2]
        ]
        return spec.get("expect_answer", ""), False, None, citations


def run_eval(
    dataset: dict,
    retriever,
    llm=None,
    top_k: int = 5,
) -> EvalReport:
    """跑一轮评测。"""
    cases = dataset["cases"]
    llm = llm or MockLLM(cases)
    results: list[EvalCaseResult] = []
    start = time.time()

    for case in cases:
        r = EvalCaseResult(
            case_id=case["case_id"],
            category=case["category"],
            difficulty=case["difficulty"],
            should_refuse=case.get("should_refuse", False),
        )
        # 挂载期望值供指标函数使用
        r.expect_clauses = case.get("expect_clauses", [])  # type: ignore[attr-defined]

        try:
            r.retrieved = retriever.search(case["question"], top_k=top_k)
            answer, refused, reason, citations = llm.answer(
                case["case_id"], case["question"], r.retrieved
            )
            r.answer = answer
            r.refused = refused
            r.refuse_reason = reason
            r.citations = citations

            r.recall_at_k = recall_at_k(r, top_k)
            r.hit = hit_at_k(r, top_k)
            r.reciprocal_rank = reciprocal_rank(r)
            r.citation_correct = citation_accuracy(r)
            r.correct_refusal = refusal_correctness(r)
        except Exception as e:  # noqa: BLE001
            r.error = f"{type(e).__name__}: {e}"

        results.append(r)

    return aggregate(results, duration=time.time() - start)


def save_report(report: EvalReport, tag: str = "baseline") -> dict:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    (REPORTS_DIR / f"{tag}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (REPORTS_DIR / f"{tag}.md").write_text(report.to_markdown(), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="HazardGuard 评测流水线")
    parser.add_argument("--dataset", default="regression_set")
    parser.add_argument(
        "--retriever",
        choices=["mock", "real"],
        default="mock",
        help="mock=验证框架本身；real=跑真实检索（需先导入法规数据）",
    )
    parser.add_argument("--tag", default="baseline", help="报告文件名")
    parser.add_argument("--compare", help="与指定基线对比，如 baseline")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    cases = dataset["cases"]

    if args.retriever == "real":
        from app.data.seed import load_seed_chunks

        knowledge = load_seed_chunks()
        if not knowledge:
            print("未导入法规种子数据，请先运行 seed 脚本", file=sys.stderr)
            return 2
        retriever = RealRetriever(knowledge)
    else:
        retriever = MockRetriever(cases)

    report = run_eval(dataset, retriever, top_k=args.top_k)
    save_report(report, args.tag)

    print(report.to_markdown())

    if args.compare:
        base_path = REPORTS_DIR / f"{args.compare}.json"
        if not base_path.exists():
            print(f"\n基线不存在: {base_path}", file=sys.stderr)
            return 2
        baseline = json.loads(base_path.read_text(encoding="utf-8"))["metrics"]
        diff = compare(baseline, report.metrics)
        print("\n## 与基线对比")
        for k, v in diff.items():
            flag = {"improve": "↑", "regress": "↓", "same": "="}[v["status"]]
            print(f"  {flag} {k:<22} {v['baseline']:.2%} → {v['current']:.2%}")
        try:
            assert_no_regression(baseline, report.metrics)
            print("\n无回退，通过。")
        except AssertionError as e:
            print(f"\n❌ {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())