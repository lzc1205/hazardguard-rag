"""前端接口与后端路由的契约校验。

为什么要有这个脚本：
前端 `api/index.ts` 里手写了 30+ 个路径，而后端路由是装饰器定义的。
两边各改各的，没有任何机制保证同步——拼错一个路径、
后端改了参数名、前端调了已删除的接口，都只能等用户点按钮才发现。
这类问题在开发期完全静默。

做法：启动应用拿到真实的 app.routes（FastAPI 新版 `_IncludedRouter`
是延迟展开的，必须递归读取 `original_router.routes`），
把「方法 + 路径模板」归一化成集合，
再逐个检查前端调用能否命中（路径里的 `{id}` 视为通配）。

用法：
    cd D:\\123456789\\PythonProject6\\backend
    ..\\.venv\\Scripts\\python.exe scripts\\check_api_contract.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_API = ROOT / "frontend" / "src" / "api"

sys.path.insert(0, str(ROOT / "backend"))


def collect_routes(app) -> set[tuple[str, str]]:
    """收集所有路由的 (METHOD, 路径模板)。

    **用 app.openapi() 而不是遍历 app.routes**：
    新版 FastAPI 把 include_router 的结果包成 `_IncludedRouter`，
    且层层嵌套，内部结构（original_router.routes）随版本变化，
    直接遍历会收集到 0 条。OpenAPI 规范是框架自己展开后的产物，
    稳定且带完整前缀，是唯一可靠的来源。
    """
    spec = app.openapi()
    found: set[tuple[str, str]] = set()
    for path, ops in (spec.get("paths") or {}).items():
        for method in ops:
            if method.upper() in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                found.add((method.upper(), path))
    return found


def normalize(path: str) -> str:
    """把具体 ID 归一化成 {param}，便于与后端模板比对。"""
    return re.sub(r"\$\{[^}]+\}", "{param}", path)


def template_matches(template: str, actual: str) -> bool:
    """后端模板是否能匹配前端的具体路径。

    要求段数一致，且模板的每一段要么是字面量相等，要么是参数。
    """
    t = template.strip("/").split("/")
    a = actual.strip("/").split("/")
    if len(t) != len(a):
        return False
    for tp, ap in zip(t, a):
        if tp.startswith("{") and tp.endswith("}"):
            continue
        if tp != ap:
            return False
    return True


def main() -> int:
    from app.main import app

    routes = collect_routes(app)
    # OpenAPI 已带 /api/v1 前缀，无需再拼
    api_routes = routes

    print(f"后端路由数：{len(api_routes)}\n")

    # 抽取前端调用
    calls: list[tuple[str, str, int]] = []
    for f in sorted(FRONTEND_API.glob("*.ts")):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(
            r"(get|post|put|delete|patch)(?:<[^>]*>)?\(\s*([`'\"][^`'\"]+[`'\"])",
            text,
        ):
            verb, raw = m.group(1).upper(), m.group(2).strip("`'\"")
            if "${" in raw:
                continue  # 模板字符串，路径前缀单独看
            calls.append((verb, raw, f.name))

    # 模板字符串另取前缀
    tpl_calls: list[tuple[str, str, str]] = []
    for f in sorted(FRONTEND_API.glob("*.ts")):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(
            r"(get|post|put|delete|patch)(?:<[^>]*>)?\(\s*`([^`$]*)\$\{[^}]+\}([^`]*)`",
            text,
        ):
            verb, prefix, suffix = m.group(1).upper(), m.group(2), m.group(3)
            tpl_calls.append((verb, f"{prefix}{{param}}{suffix}", f.name))

    all_calls = calls + tpl_calls

    missing: list[str] = []
    ok = 0
    for verb, path, src in all_calls:
        full = path if path.startswith("/api") else f"/api/v1{path}"
        hit = any(
            m == verb and template_matches(t, full) for m, t in api_routes
        )
        if hit:
            ok += 1
        else:
            same_path_other_method = [
                m for m, t in api_routes if template_matches(t, full)
            ]
            hint = (
                f"（该路径存在但方法是 {same_path_other_method}）"
                if same_path_other_method
                else "（路径不存在）"
            )
            missing.append(f"  {verb:6s} {full:52s} [{src}]{hint}")

    print(f"前端调用 {len(all_calls)} 个接口")
    print(f"匹配成功 {ok} 个，缺失 {len(missing)} 个\n")

    if missing:
        print("缺失明细：")
        for m in missing:
            print(m)
        return 1

    print("全部前端调用均能命中后端路由。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
