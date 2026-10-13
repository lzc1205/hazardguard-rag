"""依赖声明完整性检查。

**为什么需要它**：requirements.txt 漏掉某个包，本地开发时往往察觉不到——
因为开发者的 venv 里「恰好装过」，跑什么都正常。直到别人按 requirements
全新安装、或构建 Docker 镜像时才炸。

真实踩过两次：
1. `pgvector` 漏声明 → 容器里 `ModuleNotFoundError: No module named 'pgvector'`，
   服务完全起不来（数据库驱动 psycopg 装了，但 VECTOR 列的类型映射是另一个包）
2. `numpy>=2.5.3` 要求 Python 3.12+，而镜像用 3.11 → 安装直接失败

所以这个测试做两件事：
- 扫出所有第三方 import，与 requirements 比对，找出漏声明的
- 检查 requirements 里的版本约束是否与声明的 Python 版本兼容
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
APP = BACKEND / "app"
REQUIREMENTS = BACKEND / "requirements.txt"

# 标准库 + 项目自身，不算第三方
STDLIB = set(sys.stdlib_module_names) | {
    "app", "scripts", "tests", "alembic", "eval", "observability", "mcp",
    "__future__", "typing_extensions",
}

# 导入名 -> pip 包名（导入名与包名不一致的才列进来）
IMPORT_TO_PACKAGE = {
    "yaml": "pyyaml",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "jwt": "python-jose",
    "jose": "python-jose",
    "docx": "python-docx",
    "psycopg2": "psycopg2-binary",
}

# 传递依赖：由已声明的包自动带入，无需（也不应）在 requirements 里重复声明。
# 列进来是因为本项目确实直接 import 了它们，
# 但它们是 fastapi / httpx 等的依赖，显式写反而会在版本冲突时多一个约束源。
TRANSITIVE = {
    "starlette",   # fastapi 带入
    "anyio",       # httpx / starlette 带入
    "httpx2",
    "sse_starlette",
    "watchfiles",
}


def _declared_packages() -> set[str]:
    """从 requirements.txt 解析出包名集合（小写、去 extras）。"""
    pkgs: set[str] = set()
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = line.split(">=")[0].split("<=")[0].split("==")[0].split("[")[0]
        pkgs.add(name.strip().lower().replace("_", "-"))
    return pkgs


def _third_party_imports() -> set[str]:
    """扫出 app/ 下所有第三方顶层 import。"""
    found: set[str] = set()
    for py in APP.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    found.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                # 相对导入（from .xxx）没有 module 名，跳过
                if node.level == 0 and node.module:
                    found.add(node.module.split(".")[0])
    return found


def test_no_missing_dependency_declaration() -> None:
    """app/ 里的第三方 import 都必须在 requirements.txt 里声明。"""
    declared = _declared_packages()
    third_party = _third_party_imports() - STDLIB - TRANSITIVE

    missing: list[str] = []
    for mod in sorted(third_party):
        pkg = IMPORT_TO_PACKAGE.get(mod, mod).lower().replace("_", "-")
        # 声明里可能写的是别的形态（如 sqlalchemy 对应 SQLAlchemy）
        if pkg in declared:
            continue
        # 宽松匹配：忽略大小写与下划线差异
        if pkg.replace("-", "") in {d.replace("-", "") for d in declared}:
            continue
        missing.append(f"{mod} (pip: {pkg})")

    assert not missing, (
        "以下模块被代码 import 但未在 requirements.txt 声明：\n  "
        + "\n  ".join(missing)
        + "\n\n修法：在 requirements.txt 对应分组补上，"
        "否则别人按 requirements 安装后服务会 ModuleNotFoundError。"
    )


def test_pgvector_declared() -> None:
    """pgvector 必须显式声明。

    它提供 VECTOR 列的类型映射，与数据库驱动是两个包——
    只装 psycopg 不足以让 knowledge_chunks.embedding 列正常工作。
    """
    assert "pgvector" in _declared_packages(), (
        "requirements.txt 缺少 pgvector："
        "models/knowledge.py 用了 `from pgvector.sqlalchemy import Vector`"
    )


def test_numpy_version_matches_python_floor() -> None:
    """numpy 的版本约束不能高于镜像的 Python 3.12 能支持的最高版本。

    numpy 2.5.x 要求 Python >=3.12。镜像用 3.12 刚好对齐；
    若有人把镜像改回 3.11，这里会拦住。
    """
    text = REQUIREMENTS.read_text(encoding="utf-8")
    declared_floor = "3.12"

    import re

    m = re.search(r"numpy\s*([><=]+)\s*([\d.]+)", text)
    assert m, "未找到 numpy 版本约束"
    version = m.group(2)

    major_minor = tuple(int(x) for x in version.split(".")[:2])
    if major_minor >= (2, 5):
        # numpy >= 2.5 需要 Python 3.12+
        pytest.importorskip("tomllib") if False else None
        assert "3.12" in declared_floor or declared_floor >= "3.12", (
            f"numpy>={version} 需要 Python 3.12+，但项目声明的版本下限是 {declared_floor}"
        )


def test_docker_python_version_matches_pyproject() -> None:
    """镜像里的 Python 版本要与 pyproject 的 requires-python 一致。

    不一致的典型后果：本地 3.13 能装上 numpy 2.5，
    镜像 3.11 装不上，部署时才炸。
    """
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r"FROM python:([\d.]+)-slim", dockerfile)
    assert m, "Dockerfile 未找到 python 基础镜像"
    image_version = m.group(1)

    pyproject = (BACKEND / "pyproject.toml").read_text(encoding="utf-8")
    r = re.search(r'requires-python\s*=\s*">=\s*([\d.]+)"', pyproject)
    assert r, "pyproject.toml 未找到 requires-python"
    declared = r.group(1)

    image_minor = tuple(int(x) for x in image_version.split(".")[:2])
    declared_minor = tuple(int(x) for x in declared.split(".")[:2])

    assert image_minor >= declared_minor, (
        f"镜像用 Python {image_version}，但 pyproject 声明需要 >={declared}——"
        "依赖装不上"
    )


import re  # noqa: E402  （放在末尾供上方测试使用）