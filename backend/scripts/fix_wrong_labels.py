"""修正知识库中的规范标注错误，并同步更新评测集期望值。

## 要修的��么问题

种子数据里有 5 条标注为「住建部令第37号 第3条」的条目，
但内容实际出自**建办质〔2018〕31号附件**，与37 号令原文无关。

核对依据（住建部令第37号，中国政府网 gov.cn 原文）：
- **第3条**：「本规定所称危险性较大的分部分项工程（以下简称"危大工程"），
  是指……容易导致人员群死群伤或者造成重大经济损失的分部分项工程。
  危大工程及超过一定规模的危大工程范围由国务院住房城乡建设主管部门制定。」
  —— 这是**定义条款**，不含各类工程的具体范围。
- **第11条**：「专项施工方案应当由施工单位技术负责人审核签字……」

而库里那5 条装的是基坑 3m/5m、吊装 10kN、脚手架 24m 等**具体范围**，
这些内容出自 31 号文附件1/附件2。

**危害**：Agent 引用「第3条」时会引到错误的规范。
在合规审查系统里，引用错条款比指标低几个点严重得多。

## 为什么删除而不是改标注

这 5 条是「多条合并」写法（例如脚手架那条把一、二、三项合并成一句），
而**官方分条版本已经在库里**（附件1/附件2 共 40 条，均抓自官网原文）。
保留它们等于同一内容存两份，且合并版本与分条版本的条款号体系不一致。

## 用法

    python scripts/fix_wrong_labels.py --dry-run   # 先看映射
    python scripts/fix_wusal.py --apply# 实际写入
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "backend" / "app" / "data" / "seed" / "knowledge_entries.json"
DATASET = ROOT / "backend" / "eval" / "datasets" / "regression_set.json"

# 这 5 条错标条目的识别特征：37号令 + 内容含范围定义句式
WRONG_MARKERS = ("属于危险性较大", "属于超过一定规模")

# 第二类错误：内容根本不在 37 号令原文里，来源是其他规范（如《爆破安全规程》
# 或行业标准的编写惯例），却被标成 37 号令的某一条。
#
# 核对依据（gov.cn 原文全文检索）：
#   - 37 号令全文**不含「爆破」**；第七条原文是
#     「建设单位应当组织勘察、设计等单位在施工招标文件中列出危大工程清单」
#   - 全文**不含「专项施工方案应当包括」「应当包括下列内容」**
#     （第十一条原文是「专项施工方案应当由施工单位技术负责人审核签字」）
#   - 全文不含「拆除工程应编制专项拆除方案」
#
# 这类条目不能靠改条款号修复——它们的来源规范都不明确，
# 留着只会让 Agent 引用出一条不存在的「37 号令第7条」。
SOURCE_UNKNOWN_MARKERS = (
    "爆破",
    "专项施工方案应当包括",
    "拆除工程应编制专项拆除方案",
)

# 评测集里依赖「第3条」的用例 → 应指向的官方附件条款
# 依据：逐条核对用例提问的语义与官方原文
DATASET_FIXES: dict[str, list[str]] = {
    # 「基坑工程开挖深度达到多少米属于危大工程？超过多少米属超规模」
    # → 官方附件1-基坑（一）(3m) 与 附件2-深基坑(5m) 两条对应。
    # 保留原有的 JGJ 120-2012 第3.1.3条 —— 它属于待补的行业标准（P1），
    # 与本次修正无关，不应在这里被顺手删掉。
    "fp_001": [
        "附件1-基坑工程（一）",
        "附件2-深基坑工程",
        "JGJ 120-2012 第3.1.3条",
    ],
    # 「深基坑工程一般指开挖深度多少米以上？」→ 超规模档5m
    "fp_002": ["附件2-深基坑工程"],
    # 「起重吊装工程中单件起吊重量达到多少属于危大工程？」→ 10kN
    "lf_001": ["附件1-起重吊装及起重机械安装拆卸工程（一）"],
    # 「高度多少米及以上的落地式钢管脚手架属于危大工程？」→ 24m
    "sc_001": ["附件1-脚手架工程（一）"],
    # 「暗挖工程采用矿山法施工的适用范围是什么？」→ 附件1暗挖工程
    "ud_001": ["附件1-暗挖工程"],
}


def find_wrong_entries(entries: list[dict]) -> list[dict]:
    """找出两类错误标注的条目。"""
    out = []
    for e in entries:
        if e.get("regulation_code") != "住建部令第37号":
            continue
        content = e.get("content", "")
        # 第一类：装的是31 号文附件的范围条款
        if any(m in content for m in WRONG_MARKERS):
            out.append(e)
            continue
        # 第二类：内容根本不在 37 号令原文里，来源规范不明确
        if any(m in content for m in SOURCE_UNKNOWN_MARKERS):
            out.append(e)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    data = json.loads(SEED.read_text(encoding="utf-8"))
    entries = data["entries"] if isinstance(data, dict) else data
    wrong = find_wrong_entries(entries)

    print("=" * 60)
    print(f"检出错误标注条目：{len(wrong)} 条")
    print("=" * 60)
    for e in wrong:
        print(f"  [{e['clause_number']}] {e['title'][:36]}")
        print(f"      {e['content'][:62]}")

    # 校验：删除后，评测集用到的条款是否都还在库里
    remaining = [e for e in entries if e not in wrong]
    clause_set = {e["clause_number"] for e in remaining}

    print()
    print("=" * 60)
    print("评测集期望值同步方案")
    print("=" * 60)
    ds = json.loads(DATASET.read_text(encoding="utf-8"))
    problems = []
    for c in ds["cases"]:
        cid = c["case_id"]
        if cid in DATASET_FIXES:
            new = DATASET_FIXES[cid]
            # JGJ 系列属待补的行业标准（P1），本来就不在库里，不参与校验
            missing = [x for x in new if x not in clause_set and "JGJ" not in x]
            status = "OK " if not missing else "缺失"
            print(f"  [{status}] {cid}: {c.get('expect_clauses')} → {new}")
            if missing:
                problems.append((cid, missing))
        elif any(e == "第3条" for e in (c.get("expect_clauses") or [])):
            problems.append((cid, ["第3条 未在映射表中"]))
            print(f"  [待定] {cid}: 仍依赖「第3条」但未提供映射")

    if problems:
        print()
        print("以下用例需人工确认后再执行：")
        for cid, why in problems:
            print(f"  {cid}: {why}")

    if not args.apply:
        print()
        print("[试运行] 未写入。确认无误后加 --apply")
        return 0

    if problems:
        print()
        print("存在待确认项，已终止。")
        return 1

    # 写入：删除错标条目
    kept = [e for e in entries if e not in wrong]
    out = data if isinstance(data, dict) else {}
    out["entries"] = kept
    SEED.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[知识库] {len(entries)} → {len(kept)} 条（删除 {len(wrong)} 条错标）")

    # 写入：更新评测集期望值
    changed = 0
    for c in ds["cases"]:
        if c["case_id"] in DATASET_FIXES:
            c["expect_clauses"] = DATASET_FIXES[c["case_id"]]
            changed += 1
    ds.setdefault("meta", {})["label_fix"] = (
        "2026-10-13 修正：移除 5 条误标为「37号令第3条」实为 31号文附件的条目，"
        "对应用例期望值改指官方附件编号"
    )
    DATASET.write_text(json.dumps(ds, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[评测集] 更新 {changed} 个用例期望值")
    return 0


if __name__ == "__main__":
    sys.exit(main())