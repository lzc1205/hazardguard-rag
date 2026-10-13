"""前后端契约与全流程的自动化守护。

这两个问题都是「肉眼看界面发现不了」的类型：
1. 前端改了接口路径、后端改了路由 —— 只有点按钮时才发现 404
2. 某个接口内部 AttributeError —— 页面直接白屏

所以固化成测试，纳入常规 pytest 流程。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# 本文件在 backend/tests/test_api/ 下，项目根需要上溯 3 层
ROOT = Path(__file__).resolve().parents[3]
FRONTEND_API = ROOT / "frontend" / "src" / "api"


@pytest.fixture(scope="module")
def api_routes() -> set[tuple[str, str]]:
    """从 OpenAPI 规范取全部 (METHOD, 路径)。

    不要遍历 app.routes：新版 FastAPI 把 include_router 包成
    `_IncludedRouter` 且层层嵌套，直接遍历会收集到 0 条。
    """
    sys.path.insert(0, str(ROOT / "backend"))
    from app.main import app

    spec = app.openapi()
    out: set[tuple[str, str]] = set()
    for path, ops in (spec.get("paths") or {}).items():
        for method in ops:
            if method.upper() in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                out.add((method.upper(), path))
    assert out, "未能从 OpenAPI 取到任何路由"
    return out


def _normalize(path: str) -> str:
    return re.sub(r"\$\{[^}]+\}", "{param}", path)


def _matches(template: str, actual: str) -> bool:
    t = template.strip("/").split("/")
    a = actual.strip("/").split("/")
    if len(t) != len(a):
        return False
    return all(
        tp.startswith("{") or tp == ap for tp, ap in zip(t, a)
    )


def test_frontend_calls_match_backend_routes(
    api_routes: set[tuple[str, str]],
) -> None:
    """前端每个 API 调用都必须命中后端真实路由。"""
    calls: list[tuple[str, str]] = []

    for f in sorted(FRONTEND_API.glob("*.ts")):
        text = f.read_text(encoding="utf-8")
        # 普通字符串与模板字符串两种形式都要抓
        for m in re.finditer(
            r"(get|post|put|delete|patch)(?:<[^>]*>)?\(\s*"
            r"(?:`([^`$]*)\$\{[^}]+\}([^`]*)`|['\"]([^'\"]+)['\"])",
            text,
        ):
            verb = m.group(1).upper()
            if m.group(2) is not None:
                path = f"{m.group(2)}{{param}}{m.group(3)}"
            else:
                path = m.group(4)
            calls.append((verb, path))

    assert len(calls) > 30, f"只抓到 {len(calls)} 个调用，正则可能失效"

    missing = []
    for verb, path in calls:
        full = path if path.startswith("/api") else f"/api/v1{path}"
        if not any(m == verb and _matches(t, full) for m, t in api_routes):
            missing.append(f"{verb} {full}")

    assert not missing, "前端调用了后端不存在的接口：\n" + "\n".join(missing)


def test_project_update_schema_has_code() -> None:
    """ProjectUpdate 必须含 code。

    回归：曾漏掉该字段，而 update_project() 里直接读 payload.code 查重，
    导致「更新项目」接口必然 500。
    """
    sys.path.insert(0, str(ROOT / "backend"))
    from app.schemas.review import ProjectUpdate

    fields = set(ProjectUpdate.model_fields)
    assert "code" in fields, "ProjectUpdate 缺少 code 字段，会导致更新项目接口 500"
    assert "name" in fields


def test_review_workbench_uses_cancellable_polling() -> None:
    """审查工作台必须用可取消轮询，避免组件卸载后仍改写状态。

    回归：原先是裸 for + setTimeout 循环，离开页面后仍会跑完 180 次，
    持续发请求并覆盖进度条。
    """
    src = ROOT / "frontend" / "src" / "views" / "review" / "ReviewWorkbenchView.vue"
    text = src.read_text(encoding="utf-8")
    assert "usePolling" in text, "工作台未使用 usePolling"
    assert "stopPolling" in text, "工作台提交新任务前未停止旧轮询"

    composable = ROOT / "frontend" / "src" / "composables" / "usePolling.ts"
    assert composable.exists(), "usePolling.ts 不存在"
    ctext = composable.read_text(encoding="utf-8")
    assert "onUnmounted" in ctext, "usePolling 未在组件卸载时停止轮询"
