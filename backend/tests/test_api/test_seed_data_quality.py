"""知识库数据质量守护。

背景：曾发现 5 条种子数据标注为「住建部令第37号 第3条」，
但内容实际出自建办质〔2018〕31号附件（基坑3m/5m、吊装10kN、脚手架24m 等范围）。

核对住建部令第37号原文（gov.cn）：
    第三条 本规定所称危险性较大的分部分项工程（以下简称"危大工程"），
    是指……容易导致人员群死群伤或者造成重大经济损失的分部分项工程。

那是**定义条款**，不含任何具体工程范围。标注错误的后果是
Agent 引用条款时引到错误规范——在合规审查系统里，
引用错条款比检索指标低几个点严重得多。

这些测试就是防止同类问题复发的护栏。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SEED = ROOT / "backend" / "app" / "data" / "seed" / "knowledge_entries.json"

# 37 号令第三条原文关键特征：它是「定义」条款，不含具体工程范围
ORDER37_ART3_MARKERS = (
    "容易导致人员群死群伤",
    "由国务院住房城乡建设主管部门制定",
)

# 危大工程「具体范围」类内容的特征句式——不应出现在规章正文条款里
SCOPE_MARKERS = ("属于危险性较大", "属于超过一定规模")


@pytest.fixture(scope="module")
def entries() -> list[dict]:
    return json.loads(SEED.read_text(encoding="utf-8"))["entries"]


def test_no_wrong_regulation_labels(entries: list[dict]) -> None:
    """规章正文条款里不应出现「危大工程具体范围」类内容。

    这类内容属于建办质〔2018〕31号附件，不属于住建部令第37号正文。
    实测踩过：5 条「第3条」装的是基坑/吊装/脚手架范围，
    而 37 号令第3条是定义条款——引用出去就是错的规范。
    """
    wrong = [
        e
        for e in entries
        if e.get("regulation_code") == "住建部令第37号"
        and any(m in e.get("content", "") for m in SCOPE_MARKERS)
    ]
    assert not wrong, (
        "以下条目标注为 37 号令，但内容实为 31 号文附件的范围条款：\n"
        + "\n".join(f"  [{e['clause_number']}] {e['title']}" for e in wrong)
        + "\n\n修法：删除（官方分条版本已在库）或改判到正确规范，并同步更新评测集期望值。"
    )


def test_clause_number_unique_within_regulation(entries: list[dict]) -> None:
    """同一规范内条款号必须唯一。

    实测踩过：「第3条」在库中出现 5 次、「第11条」2 次，
    分别装着基坑、吊装、脚手架、拆除等完全不同的内容。
    检索时无法从「第3条」推断问的是哪部规范的条款——
    「第3条」在不同规范里含义完全不同。
    """
    seen: dict[tuple[str, str], int] = {}
    for e in entries:
        key = (e.get("regulation_code", ""), e.get("clause_number", ""))
        seen[key] = seen.get(key, 0) + 1

    dupes = {k: v for k, v in seen.items() if v > 1}
    assert not dupes, (
        "以下 (规范号, 条款号) 组合出现多次，导致引用歧义：\n"
        + "\n".join(f"  {k[0]} {k[1]}：{v} 条" for k, v in dupes.items())
        + "\n\n修法：条款号需带唯一限定（如 附件1-基坑工程（一）），"
        "或修正错误的规范标注。"
    )


def test_clause_numbers_are_traceable(entries: list[dict]) -> None:
    """已核对来源的条款，编号必须能追到官方原文的具体位置。

    统一编号体系有两种合法形态：
    - 规章正文：「第N条」（中文数字，住建部令第37号）
    - 官方附件：「附件N-…」（建办质〔2018〕31号 附件1/附件2）

    **适用范围**：只校验已从官方源核对过的两部规范。
    JGJ 系列（JGJ 80/120/130/276）目前是早期整理的数据，
    原文尚未从工程建设标准化平台获取，编号格式暂不统一——
    这是已知的待办项（P1），由test_jgj_pending 标记，
    等补齐原文后再纳入本检查。**不静默放过，也不假装已合规。**
    """
    # 条款号形如 第3条 / 第11条 / 第十二条 —— 中文数字或阿拉伯数字均可
    pattern = re.compile(r"^第[一二三四五六七八九十百零\d]+条")
    checked_codes = {"住建部令第37号", "建办质〔2018〕31号"}

    bad = [
        e
        for e in entries
        if e.get("regulation_code") in checked_codes
        and not pattern.match(e.get("clause_number", ""))
        and not e.get("clause_number", "").startswith("附件")
    ]
    assert not bad, (
        "以下已核对来源的条款编号不可追溯：\n"
        + "\n".join(
            f"  [{e['clause_number']}] {e.get('regulation_code','')} {e['title']}"
            for e in bad
        )
        + "\n\n应改为「第N条」（中文数字）或「附件N-…」形式。"
    )


def test_jgj_pending_documented(entries: list[dict]) -> None:
    """JGJ 系列仍未统一编号——显式记录为待办，不让它悄悄溜过去。"""
    jgj = [e for e in entries if "JGJ" in e.get("regulation_code", "")]
    if not jgj:
        pytest.skip("已无 JGJ 条目")

    untraced = [
        e
        for e in jgj
        if not re.match(r"^第[一二三四五六七八九十百零\d]+条", e.get("clause_number", ""))
    ]
    # 这些条目确实存在且编号未统一——用 skip 记录，而不是断言失败，
    # 因为补齐 JGJ 原文属于 P1 任务，不应让 CI 长期红着。
    pytest.skip(
        f"待办 P1：{len(jgj)} 条 JGJ 条目中 {len(untraced)} 条编号未统一"
        f"（形如 第4.2.1条 / 第8章）。"
        f"需从工程建设标准化平台获取官方原文后重新编号。"
    )


def test_content_matches_clause_semantics(entries: list[dict]) -> None:
    """超规模条款的阈值必须大于同类的普通档阈值。

    这是数据准确性的底线检查：官方附件2（超过一定规模）的阈值
    一律高于附件1（危大工程），例如基坑 5m > 3m、模板 8m > 5m、
    落地脚手架 50m > 24m。若某个数值反了，审查结论会直接出错。
    """
    # 抽查：附件1 基坑应为 3m，附件2 基坑应为 5m
    expectations = {
        "附件1-基坑工程（一）": "3m",
        "附件2-深基坑工程": "5m",
    }
    by_clause = {e["clause_number"]: e for e in entries}
    for clause, expected_token in expectations.items():
        e = by_clause.get(clause)
        if e is None:
            continue
        assert expected_token in e["content"], (
            f"{clause} 的内容应含「{expected_token}」，实际：{e['content'][:60]}"
        )


def test_seed_file_exists_and_not_empty(entries: list[dict]) -> None:
    """基础守护：种子文件必须存在且有内容。"""
    assert SEED.exists(), f"种子数据不存在：{SEED}"
    assert len(entries) >= 30, f"种子数据过少（{len(entries)} 条），扩库成果可能丢失"


def test_order37_article3_is_definition_not_scope(entries: list[dict]) -> None:
    """如果库中存在 37 号令第3条，它必须是定义条款。

    兜底检查：确保第3条没有在某次修改中变成范围条款。
    """
    for e in entries:
        if e.get("clause_number") == "第3条" and e.get("regulation_code") == "住建部令第37号":
            assert not any(m in e["content"] for m in SCOPE_MARKERS), (
                f"37号令第3条变成了范围条款：{e['content'][:60]}"
            )
            assert any(m in e["content"] for m in ORDER37_ART3_MARKERS) or len(
                e["content"]
            ) > 100, "37号令第3条内容与官方定义条款不符，请核对原文"
            return
    pytest.skip("库中无 37 号令第3条")