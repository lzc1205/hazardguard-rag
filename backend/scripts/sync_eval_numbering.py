"""把评测集与知识库统一到官方附件编号体系。

做三件事：
1. 校验映射目标在抓取数据中真实存在（防止写入悬空期望值）
2. 把旧编号替换为官方编号
3. 报告哪些期望仍未覆盖（JGJ 系列等），不做静默处理

用法：
    python scripts/sync_eval_numbering.py --dry-run   # 先看要改什么
    python scripts/sync_eval_numbering.py --apply    # 实际写入
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from eval.clause_alias import CLAUSE_ALIAS, is_mappable, map_clause  # noqa: E402

DATASET = ROOT / "backend" / "eval" / "datasets" / "regression_set.json"
PARSED = ROOT / ".cache" / "legal" / "parsed.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入（默认只报告）")
    args = ap.parse_args()

    if not PARSED.exists():
        print(f"找不到 {PARSED}\n先运行 fetch_legal_text.py")
        return 1

    # 1) 校验映射目标真实存在
    parsed = json.loads(PARSED.read_text(encoding="utf-8"))["entries"]
    valid_clauses = {e["clause_number"] for e in parsed}

    print("=" * 60)
    print("映射目标校验")
    print("=" * 60)
    bad = []
    for old, new in CLAUSE_ALIAS.items():
        if new not in valid_clauses:
            bad.append((old, new))
            print(f"  [失效] {old} → {new}（抓取数据中不存在）")
    if not bad:
        print("  全部映射目标均存在于抓取数据中")
    if bad:
        print("\n存在失效映射，终止。修 clause_alias.py 后重试。")
        return 1

    # 2) 统计将被修改的用例
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    cases = data["cases"]

    # 「对不上」的判定必须跨**两份规范**看：
    # 期望值既有 31 号文的附件编号，也有 37 号令的「第X条」。
    # 只拿 31 号文清单去校验，会把「第3条」这类 37 号令条款误报为缺失
    # （实测踩过：第3/7/11/12/13条 其实都在库里）。
    SEED = ROOT / "backend" / "app" / "data" / "seed" / "knowledge_entries.json"
    seed_clauses: set[str] = set()
    if SEED.exists():
        for e in json.loads(SEED.read_text(encoding="utf-8"))["entries"]:
            if "37号" in e["regulation_code"]:
                seed_clauses.add(e["clause_number"])

    will_change = []
    unresolved = []
    for c in cases:
        for i, clause in enumerate(c.get("expect_clauses") or []):
            if is_mappable(clause):
                will_change.append((c["case_id"], clause, map_clause(clause)))
            elif (
                clause.strip() not in valid_clauses
                and clause.strip() not in seed_clauses
                and "JGJ" not in clause
            ):
                # 不在映射表、也不在任何已知清单里 → 记录待人工确认
                unresolved.append((c["case_id"], clause))

    print()
    print("=" * 60)
    print(f"将被更新的期望条款：{len(will_change)} 处")
    print("=" * 60)
    for cid, old, new in will_change:
        print(f"  {cid:8s} {old:28s} → {new}")

    # 3) 报告仍未覆盖的期望
    print()
    print("=" * 60)
    print("仍未覆盖的期望条款（需另行补数据，不在本次范围）")
    print("=" * 60)
    jgj = []
    for c in cases:
        for clause in c.get("expect_clauses") or []:
            if "JGJ" in clause:
                jgj.append((c["case_id"], clause))
    seen = set()
    for cid, clause in jgj:
        if clause in seen:
            continue
        seen.add(clause)
        print(f"  {cid:8s} {clause}")
    print(f"  合计 {len(seen)} 个不同条款，涉及 {len(jgj)} 处引用")
    print("  → 属工程建设行业标准，需从标准化平台另行获取原文")

    if unresolved:
        print()
        print("另有对不上任何条款的期望（需人工确认）：")
        for cid, clause in unresolved:
            print(f"  {cid:8s} {clause}")

    if not args.apply:
        print()
        print("[试运行] 未写入。确认无误后加 --apply")
        return 0

    # 4) 写入
    changed = 0
    for c in cases:
        clauses = c.get("expect_clauses") or []
        new_list = []
        for clause in clauses:
            if is_mappable(clause):
                new_list.append(map_clause(clause))
                changed += 1
            else:
                new_list.append(clause)
        c["expect_clauses"] = new_list

    data.setdefault("meta", {})["clause_numbering"] = (
        "官方附件编号（建办质〔2018〕31号附件1/附件2）；"
        "JGJ 系列仍为标准原编号，待补齐对应标准原文后统一"
    )
    DATASET.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[写入] {DATASET}（更新 {changed} 处）")
    return 0


if __name__ == "__main__":
    sys.exit(main())