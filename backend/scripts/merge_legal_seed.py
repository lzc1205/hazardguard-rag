"""把官方抓取的条款合并进种子数据。

只做「新增」不做「修改」：
- 已存在的 (regulation_code, clause_number) 保留原条目不动
- 这样反复运行不会覆盖人工修订过的内容

## 判重只用 (规范号, 条款号)，不能按内容

建办质〔2018〕31号 的附件1 与附件2 里有 4 组条款**正文完全一致**
（官方原文如此），例如：

    附件1-暗挖工程 与 附件2-暗挖工程
    —— 都是「采用矿山法、盾构法、顶管法施工的隧道、洞室工程。」

但它们分属不同档位（普通危大 vs 超过一定规模），
阈值与处置要求都不同。少入库一条，
「这个工程要不要专家论证」就会判错档。

**实测踩过**：早期版本按内容前 40 字判重，导致这 4 条被静默跳过，
脚本报告「跳过重复 40 条」，但库里其实缺了 4 条——
静默的数据不一致比直接报错更难发现。

用法：
    python scripts/merge_legal_seed.py            # 合并
    python scripts/merge_legal_seed.py --dry-run  # 只看会加什么
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "backend" / "app" / "data" / "seed" / "knowledge_entries.json"
PARSED = ROOT / ".cache" / "legal" / "parsed.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not PARSED.exists():
        print(f"找不到 {PARSED}\n先运行 fetch_legal_text.py")
        return 1

    data = json.loads(SEED.read_text(encoding="utf-8"))
    entries = data["entries"] if isinstance(data, dict) else data
    parsed = json.loads(PARSED.read_text(encoding="utf-8"))["entries"]

    existing = {(e["regulation_code"], e["clause_number"]) for e in entries}

    added: list[dict] = []
    skipped = 0
    for item in parsed:
        key = (item["regulation_code"], item["clause_number"])
        if key in existing:
            skipped += 1
            continue
        item.setdefault("is_mandatory", True)
        added.append(item)
        existing.add(key)

    print(f"现有 {len(entries)} 条")
    print(f"待合并 {len(parsed)} 条，跳过重复 {skipped} 条")
    print(f"将新增 {len(added)} 条 → 合计 {len(entries) + len(added)} 条")

    if args.dry_run:
        print("\n[试运行] 未写入。将新增：")
        for cat, n in Counter(a["category"] for a in added).most_common():
            print(f"  {cat}: {n}")
        return 0

    if not added:
        print("无新增，无需写入")
        return 0

    merged = entries + added
    out = data if isinstance(data, dict) else {}
    out["entries"] = merged
    SEED.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[写入] {SEED}")

    print("[合并后分类分布]")
    for cat, n in Counter(e["category"] for e in merged).most_common():
        print(f"  {cat}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())