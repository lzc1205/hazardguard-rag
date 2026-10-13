"""MCP 工具独立自测脚本。

需求文档第 11.3 节要求：「提供工具自测脚本，可脱离 LLM 验证每个工具行为」。

运行：python mcp/servers/test_tools.py

为什么需要它：
MCP 工具的行为只能通过模型调用来间接验证，一旦模型选错工具或传错参数，
很难判断是工具的问题还是模型的问题。自测脚本把工具契约固定下来，
出问题时可精确定位。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.servers.regulation_server import (
    ClauseDetailQuery,
    DocumentParseQuery,
    RegulationQuery,
    ValidityQuery,
    _check_version_validity,
    _get_clause_detail,
    _list_hazard_categories,
    _parse_document,
    _search_regulation,
)

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"

_results: list[tuple[bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((condition, name))
    print(f"  {PASS if condition else FAIL} {name}" + (f"  —— {detail}" if detail else ""))


def test_search_by_keyword() -> None:
    print("\n[1] search_regulation 关键词检索")
    r = _search_regulation(RegulationQuery(keyword="立杆间距", top_k=5))
    check("返回非空结果", len(r) > 0, f"{len(r)} 条")
    if r:
        check("首条含条款号", bool(r[0]["clause_no"]), r[0]["clause_no"])
        check("首条含规范编号", bool(r[0]["regulation_code"]), str(r[0]["regulation_code"]))
        check("首条含原文", len(r[0]["content"]) > 10)
        check("首条含引用串", "《" in r[0]["citation"])


def test_search_by_clause_no() -> None:
    print("\n[2] search_regulation 精确条款号")
    r = _search_regulation(RegulationQuery(clause_no="第4.2.1条", top_k=3))
    check("命中目标条款", any("4.2.1" in (x["clause_no"] or "") for x in r), f"{len(r)} 条")


def test_search_mandatory_only() -> None:
    print("\n[3] search_regulation 只看强制性条文")
    r = _search_regulation(RegulationQuery(keyword="严禁", only_mandatory=True, top_k=10))
    check("返回结果均含强制性标记", all(x["is_mandatory"] for x in r), f"{len(r)} 条")


def test_search_by_category() -> None:
    print("\n[4] search_regulation 类别过滤")
    r = _search_regulation(RegulationQuery(keyword="支撑", category="formwork", top_k=5))
    check("结果均属模板类", all(x["category"] == "formwork" for x in r), f"{len(r)} 条")


def test_get_clause_detail() -> None:
    print("\n[5] get_clause_detail 条款原文")
    r = _get_clause_detail(ClauseDetailQuery(regulation_code="JGJ 80-2016", clause_no="第4.2.1条"))
    check("成功取到条款", "content" in r, r.get("clause_no", r.get("error", "")))
    if "content" in r:
        check("原文非空", len(r["content"]) > 10)
    missing = _get_clause_detail(
        ClauseDetailQuery(regulation_code="JGJ 80-2016", clause_no="第99.99条")
    )
    check("不存在的条款返回错误", "error" in missing)


def test_check_version_validity() -> None:
    print("\n[6] check_version_validity 时效性校验")
    ok = _check_version_validity(ValidityQuery(regulation_code="JGJ 80-2016"))
    check("已收录规范标记为现行", ok["is_active"] is True, ok.get("reason", ""))
    unknown = _check_version_validity(ValidityQuery(regulation_code="JGJ 999-9999"))
    check("未收录规范标记为不可用", unknown["is_active"] is False, unknown.get("reason", ""))


def test_list_categories() -> None:
    print("\n[7] list_hazard_categories 七大类别")
    r = _list_hazard_categories()
    check("返回 7 个类别", len(r) == 7, f"{len(r)} 个")
    check("全部含危大阈值", all(x["hazard_threshold"] for x in r))
    check("全部含超规模阈值", all(x["over_scale_threshold"] for x in r))
    keys = {x["key"] for x in r}
    expected = {
        "foundation_pit",
        "formwork",
        "lifting",
        "scaffold",
        "demolition",
        "underground",
        "other",
    }
    check("类别键完整", keys == expected, str(sorted(keys)))


def test_parse_document() -> None:
    print("\n[8] parse_document 文档解析")
    tmp = Path(__file__).parent / "_tmp_test.txt"
    tmp.write_text(
        "建筑施工模板安全技术规范\n\n4.2.1 模板支撑立杆的纵横向间距，不应大于900mm。\n",
        encoding="utf-8",
    )
    r = _parse_document(DocumentParseQuery(file_path=str(tmp), include_pages=True))
    tmp.unlink(missing_ok=True)

    check("解析成功", "error" not in r, r.get("error", ""))
    check("识别为 txt", r.get("file_type") == "txt")
    check("正文非空", len(r.get("full_text", "")) > 10)
    check("返回分页信息", len(r.get("pages", [])) > 0)

    missing = _parse_document(DocumentParseQuery(file_path="C:/不存在的文件.txt"))
    check("不存在的文件返回错误", "error" in missing)


def main() -> int:
    print("=" * 60)
    print("MCP 工具自测（需求文档 11.3 节要求：可脱离 LLM 验证）")
    print("=" * 60)

    for fn in (
        test_search_by_keyword,
        test_search_by_clause_no,
        test_search_mandatory_only,
        test_search_by_category,
        test_get_clause_detail,
        test_check_version_validity,
        test_list_categories,
        test_parse_document,
    ):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            check(f"{fn.__name__} 异常", False, f"{type(e).__name__}: {e}")

    total = len(_results)
    passed = sum(1 for ok, _ in _results if ok)
    print("\n" + "=" * 60)
    print(f"结果：{passed}/{total} 通过")
    if passed < total:
        print("失败项：")
        for ok, name in _results:
            if not ok:
                print(f"  - {name}")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())