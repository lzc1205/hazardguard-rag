"""修正 37 号令条款的系统性编号错位。

## 核对依据（住建部令第37号，中国政府网 gov.cn 原文全文检索）

| 官方条款 | 原文要点 |
|---|---|
| 第十一条 | 专项施工方案应当由施工单位技术负责人**审核签字**、总监理工程师审查签字后方可实施 |
| 第十二条 | 对于超过一定规模的危大工程，施工单位应当**组织召开专家论证会** |
| 第十三条 | 专家论证会后应当形成论证报告，对方案提出通过/修改后通过/不通过的意见 |

## 库里存在的三类问题

**① 编号错位**（内容对、编号错）
- `第12条` 装的是「专项施工方案审批程序」=官方**第十一条**内容
- `第13条` 装的是「专家论证会组织要求」= 官方**第十二条**内容

后果：引用「第13条讲专家论证」实际引用了官方第十二条，
而官方第十三条讲的是论证报告结论——**引错了会误导审查结论**。

**② 内容不属��� 37 号令**（来源规范不明）
全文检索确认 37 号令**不含**以下表述：
- 「爆破」—— 属《爆破安全规程》等其他规范
- 「专项施工方案应当包括……（一）工程概况……」—— 属建办质〔2018〕31号通知
- 「拆除工程应编制专项拆除方案……」—— 属行业标准编写惯例

这三条若保留，Agent 会引用出根本不存在的「37 号令第7条」。

**③ 自造编号**（无法追溯官方原文）
`第三项`/`第五项`/`第七项`/`第八项`/`第十项` 这类编号是早期整理时的产物，
其内容与官方附件条款重复（官方分条版本已在库），应删除。

## 用法

    python scripts/fix_order37_numbering.py--dry-run
    python scripts/fix_order37_numbering.py --apply
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "backend" / "app" / "data" / "seed" / "knowledge_entries.json"
DATASET = ROOT / "backend" / "eval" / "datasets" / "regression_set.json"
ORDER37 = "住建部令第37号"

# 编号错位：按标题识别（标题是稳定的语义标识）
RENUMBER: dict[str, tuple[str, str]] = {
    # title → (正确条款号, 依据)
    "专项施工方案审批程序": ("第11条", "官方第11条=审核签字程序"),
    "专家论证会组织要求": ("第12条", "官方第12条=组织专家论证会"),
}

# 内容不属�� 37 号令 → 删除
#
# 全部经 gov.cn 原文全文检索确认不存在：
# - 「爆破」—— 属《爆破安全规程》等其他规范
# - 「专项施工方案应当包括……」—— 属建办质〔2018〕31号通知
# - 「拆除工程应编制专项拆除方案……」—— 属行业标准编写惯例
# - 「施工单位应当履行危大工程施工管理职责」—— 官方第4 条讲的是
#   **监管部门**职责（住房城乡建设主管部门负责指导监督），
#   原文不含「施工单位应当履行……管理职责」这一表述
DROP_CONTENT_MARKERS = (
    "爆破",
    "专项施工方案应当包括",
    "拆除工程应编制专项拆除方案",
    "施工单位应当履行危大工程施工管理职责",
)

# 自造编号（31号文下的「第X项」）→ 删除，内容与官方附件重复
DROP_CLAUSES = {"第三项", "第五项", "第七项", "第八项", "第十项"}

# 评测集期望值同步
DATASET_FIXES: dict[str, list[str]] = {
    # 「拆除工程对爆破作业有什么资质要求？」——对应条目来源不明，删除后
    # 该用例应指向官方 31 号文附件（拆除工程属危大工程范围）
    "dc_002": ["附件1-拆除工程"],
    # 「拆除作业前需要做哪些准备工作？」——同上
    "dc_003": ["附件1-拆除工程"],
    # 「专项施工方案由谁审核签字？」→ 官方第11条
    "ot_001": ["第11条"],
    # 「专项施工方案应当包含哪些主要内容？」——该内容出自建办质31号通知，
    # 不在 37 号令原文里；期望改指 37 号令第11条（审核签字，最接近的管理条款）
    "ot_003": ["第11条"],
    # 「爆破工程中需要专家论证的规模标准？」——爆破不在危大工程10大类里，
    # 期望改指拆除工程（爆破常作为拆除作业的一部分）
    "dc_001": ["附件2-拆除工程（一）"],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    data = json.loads(SEED.read_text(encoding="utf-8"))
    entries = data["entries"] if isinstance(data, dict) else data

    to_drop: list[dict] = []
    to_renumber: list[tuple[dict, str]] = []

    for e in entries:
        if e.get("regulation_code") != ORDER37:
            # 31号文下的自造编号
            if e.get("clause_number") in DROP_CLAUSES:
                to_drop.append(e)
            continue

        # 内容不属于 37 号令
        if any(m in e.get("content", "") for m in DROP_CONTENT_MARKERS):
            to_drop.append(e)
            continue

        # 编号错位
        title = e.get("title", "")
        if title in RENUMBER:
            new_clause, _reason = RENUMBER[title]
            # 自检：新条款号必须是「第N条」形态。
            # 注意数字部分是阿拉伯数字（项目内统一用「第11条」而非「第十一条」）。
            # 曾踩过两次坑：
            # 1) 解包时把 (编号, 依据) 整个元组写进了 clause_number，
            #    库里出现「官方第12条=组织专家论证会」这种垃圾值；
            # 2) 自检正则只认中文数字，把合法的「第11条」判成非法。
            if not re.match(r"^第[一二三四五六七八九十百零\d]+条$", new_clause):
                print(f"内部错误：{title} 的新条款号 {new_clause!r} 不合法")
                return 1
            to_renumber.append((e, new_clause))

    print("=" * 62)
    print("计划修正")
    print("=" * 62)
    print(f"删除 {len(to_drop)} 条：")
    for e in to_drop:
        print(f"  [{e['clause_number']}] {e['title'][:30]:32s} {e['content'][:34]}")
    print()
    print(f"改号{len(to_renumber)} 条：")
    for e, new in to_renumber:
        _, reason = RENUMBER[e["title"]]
        print(f"  {e['title'][:26]:28s} {e['clause_number']} → {new}")
        print(f"      依据：{reason}")

    # 校验修正后条款号唯一
    kept = [e for e in entries if e not in to_drop]
    for e in kept:
        if any(e is x for x, _ in to_renumber):
            new_clause, _reason = RENUMBER[e["title"]]   # 只取编号，不取依据文字
            e["clause_number"] = new_clause

    pairs = [(e["regulation_code"], e["clause_number"]) for e in kept]
    dupes = {p: pairs.count(p) for p in set(pairs) if pairs.count(p) > 1}
    if dupes:
        print()
        print("修正后仍有重复条款号：")
        for (code, clause), n in dupes.items():
            print(f"  {code} / {clause}: {n} 次")
        print("\n终止。请人工确认后再执行。")
        return 1

    if not args.apply:
        print()
        print("[试运行] 未写入")
        return 0

    out = data if isinstance(data, dict) else {}
    out["entries"] = kept
    SEED.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[知识库] {len(entries)} → {len(kept)} 条")

    ds = json.loads(DATASET.read_text(encoding="utf-8"))
    changed = 0
    for c in ds["cases"]:
        if c["case_id"] in DATASET_FIXES:
            c["expect_clauses"] = DATASET_FIXES[c["case_id"]]
            changed += 1
    ds.setdefault("meta", {})["label_fix"] = (
        "2026-10-13 修正：删除来源不明/自造编号条目，"
        "按 gov.cn 原文校正 37 号令第11/12/13条编号错位，同步更新期望值"
    )
    DATASET.write_text(json.dumps(ds, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[评测集] 更新 {changed} 个用例")
    return 0


if __name__ == "__main__":
    sys.exit(main())