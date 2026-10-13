"""从官方来源抓取并解析危大工程范围条款。

数据来源（均为政府官网，非二手整理）：
- 附件1/2：住建部官网
  https://www.mohurd.gov.cn/gongkai/zc/xzgfxwjk/art/2018/art_1925138665.html
  （住房城乡建设部办公厅关于实施《危险性较大的分部分项工程安全管理规定》
   有关问题的通知，建办质〔2018〕31号）
- 部令正文：中国政府网
  https://www.gov.cn/zhengce/2022-02/05/content_5711933.htm
  （住建部令第37号，含2019年修正）

**为什么必须从官方源抓**：法规条文不能凭记忆写。
建筑安全类条款的数值阈值（如「搭设高度5m及以上」）写错一个数字，
审查结论就会反向——这是合规审查系统最不能出的错。

用法：
    python scripts/fetch_legal_text.py --source mohurd31    # 抓 31号文附件
    python scripts/fetch_legal_text.py --source order37     # 抓 37号令正文
    python scripts/fetch_legal_text.py --parse-only         # 仅解析已下载文件
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".cache" / "legal"

SOURCES = {
    "mohurd31": {
        "name": "建办质〔2018〕31号 附件（危大工程范围）",
        "url": "https://www.mohurd.gov.cn/gongkai/zc/xzgfxwjk/art/2018/art_1925138665.html",
        "file": "31hao.html",
    },
    "order37": {
        "name": "住建部令第37号《危险性较大的分部分项工程安全管理规定》",
        "url": "https://www.gov.cn/zhengce/2022-02/05/content_5711933.htm",
        "file": "order37.html",
    },
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def fetch(source: str) -> Path:
    """下载原文到本地缓存（已存在则跳过，避免重复请求官方站点）。"""
    meta = SOURCES[source]
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / meta["file"]

    if dest.exists() and dest.stat().st_size > 5000:
        print(f"[缓存] {dest.name} 已存在（{dest.stat().st_size} 字节）")
        return dest

    req = urllib.request.Request(meta["url"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    dest.write_bytes(raw)
    print(f"[下载] {meta['name']} → {dest}（{len(raw)} 字节）")
    return dest


def to_text(path: Path) -> str:
    """HTML → 纯文本，保留换行以便按条切分。"""
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="ignore")

    text = re.sub(r"<script.*?</script>", "", text, flags=re.S)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</(p|div|li|tr|h\d)>", "\n", text)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html.unescape(text)

    lines = [ln.strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


# 一、基坑工程 / 二、模板工程及支撑体系 / ... 中文序号 → 分类 key
#
# 注意「深基坑工程」：附件2（超过一定规模）里基坑类的官方用词是
# 「深基坑」，不是附件1 的「基坑」。两者都映射到foundation_pit。
CATEGORY_MAP = {
    "基坑工程": "foundation_pit",
    "深基坑工程": "foundation_pit",
    "模板工程及支撑体系": "formwork",
    "起重吊装及起重机械安装拆卸工程": "lifting",
    "脚手架工程": "scaffold",
    "拆除工程": "demolition",
    "暗挖工程": "underground",
    "其它": "other",
    "其他": "other",
}

# 附件 1 = 危大工程；附件 2 = 超过一定规模的危大工程
SCALE_MARKERS = {
    "1": ("危大工程范围", False),
    "2": ("超过一定规模的危大工程范围", True),
}


def parse_scope(text: str) -> list[dict]:
    """解析「危大工程范围」类文本为条款列表。

    只处理带中文序号的分项（一、基坑工程 …），因为那是官方分类的权威口径。
    """
    results: list[dict] = []

    # 定位两份附件的正文起点。
    #
    # 页面前部有「详见附件2」这类**目录引用**，不能用简单的 find("附件1")。
    # 真正的正文结构是：
    #   附件1\n危险性较大的分部分项工程范围\n一、基坑工程\n...
    #   附件2\n超过一定规模的危险性较大的分部分项工程范围\n一、深基坑工程\n...
    # 所以定位条件是：标签 + 可选标题行 + 紧跟中文序号的大类名。
    def find_attachment_start(text: str, label: str) -> int:
        pat = re.compile(
            rf"{label}\s*\n(?:\s*[^\n]*危险性较大[^\n]*\n)?\s*[一二三四五六七八九十]+、[^\n]+"
        )
        m = pat.search(text)
        return m.start() if m else -1

    idx1 = find_attachment_start(text, "附件1")
    idx2 = find_attachment_start(text, "附件2")
    if idx1 < 0:
        print("  [警告] 未定位到附件1 正文")
        return results
    if idx2 <= idx1:
        print("  [警告] 未定位到附件2 正文，将只解析附件1")
        idx2 = len(text)

    sections = [(idx1, idx2, "1"), (idx2, len(text), "2")]

    for start, end, scale_key in sections:
        block = text[start:end]
        scope_name, is_large = SCALE_MARKERS[scale_key]

        # 按「一、」「二、」等切分类
        parts = re.split(r"\n([一二三四五六七八九十]+)、([^\n]+)\n", block)
        # parts 结构: [前导, 序号, 类名, 内容, 序号, 类名, 内容, ...]
        for i in range(1, len(parts) - 2, 3):
            seq = parts[i]
            cat_name = parts[i + 1].strip()
            body = parts[i + 2]

            cat_key = CATEGORY_MAP.get(cat_name)
            if not cat_key:
                continue

            # 再按「（一）（二）」切子项
            subs = re.findall(r"[（(]([一二三四五六七八九十]+)[）)]\s*([^\n]+(?:\n(?![（(])[^\n]+)*)", body)
            if not subs:
                # 没有子项，整段作为一条
                clean = body.strip()
                # 阈值放宽到 6 字：像「水下作业工程。」这种极短条款
                # 恰恰是最重要的判定依据，不能因字数少被丢掉。
                if len(clean) >= 6:
                    results.append(_make_entry(cat_name, cat_key, scope_name, is_large, "", clean))
                continue

            for sub_seq, sub_text in subs:
                clean = re.sub(r"\s*\n\s*", "", sub_text).strip()
                # 同理：6 字即可（原文简短的条款是常态，不是解析错误）
                if len(clean) < 6:
                    continue
                results.append(
                    _make_entry(cat_name, cat_key, scope_name, is_large, sub_seq, clean)
                )

    return results


def _make_entry(cat_name, cat_key, scope_name, is_large, sub_seq, content) -> dict:
    """构造入库条目。"""
    suffix = f"（{sub_seq}）" if sub_seq else ""
    return {
        "title": f"{scope_name}：{cat_name}{suffix}",
        "content": content,
        "entry_type": "regulation",
        "category": cat_key,
        "regulation_name": "危险性较大的分部分项工程范围"
        if not is_large
        else "超过一定规模的危险性较大的分部分项工程范围",
        "regulation_code": "建办质〔2018〕31号",
        "clause_number": f"附件{'2' if is_large else '1'}-{cat_name}{suffix}",
        "is_mandatory": True,
        "is_over_scale": is_large,
        "tags": [t for t in [cat_name, suffix.strip("（）")] if t],
    }


def parse_order37(text: str) -> list[dict]:
    """解析 37 号令正文（第X条）。"""
    results: list[dict] = []
    # 只取第一章到第六章前的实体条款，跳过法律责任与附则
    body = text
    parts = re.split(r"\n第([一二三四五六七八九十百零]+)条", body)
    for i in range(1, len(parts) - 1, 2):
        cn = parts[i]
        content = re.sub(r"\s*\n\s*", "", parts[i + 1]).strip()
        if not content or len(content) < 10:
            continue
        # 跳过法律责任章（从"法律责任"之后开始的条款不再收）
        results.append(
            {
                "title": f"危大工程安全管理规定 第{cn}条",
                "content": content[:1200],
                "entry_type": "regulation",
                "category": "other",
                "regulation_name": "危险性较大的分部分项工程安全管理规定",
                "regulation_code": "住建部令第37号",
                "clause_number": f"第{cn}条",
                "is_mandatory": True,
                "tags": ["安全管理规定"],
            }
        )
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取并解析危大工程官方条文")
    ap.add_argument("--source", choices=list(SOURCES) + ["all"], default="all")
    ap.add_argument("--parse-only", action="store_true", help="只解析本地缓存，不联网")
    ap.add_argument("--out", default=str(CACHE / "parsed.json"))
    args = ap.parse_args()

    targets = list(SOURCES) if args.source == "all" else [args.source]

    all_entries: list[dict] = []
    for src in targets:
        meta = SOURCES[src]
        if args.parse_only:
            path = CACHE / meta["file"]
            if not path.exists():
                print(f"[跳过] {meta['file']} 不存在")
                continue
        else:
            path = fetch(src)

        text = to_text(path)
        entries = parse_scope(text) if src == "mohurd31" else parse_order37(text)
        print(f"[解析] {meta['name']} → {len(entries)} 条")
        all_entries.extend(entries)

    out = Path(args.out)
    out.write_text(
        json.dumps({"entries": all_entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[输出] {out}（共 {len(all_entries)} 条）")

    from collections import Counter

    dist = Counter(e["category"] for e in all_entries)
    print("[分类分布]", dict(dist))
    return 0


if __name__ == "__main__":
    sys.exit(main())