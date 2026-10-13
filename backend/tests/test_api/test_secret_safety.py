"""密钥安全守护。

背景：
项目要求开源（JD 里 19%/25% 看重），而 `backend/.env` 里存着真实的
`LLM_API_KEY`。一旦 `git add .` 提交上去，密钥就永久留在 git 历史里，
即使之后删除文件也救不回来——这是最容易发生、后果最严重的疏漏。

三条红线：
1. .env 必须被 .gitignore 拦住，但 .env.example 必须放行
2. 代码/文档里不能出现硬编码的密钥
3. JWT 默认密钥不能是「上线即用」的值
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GITIGNORE = ROOT / ".gitignore"

# 形如 sk- 开头、20 位以上的真实密钥特征
SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9]{20,}")

# 允许出现密钥特征的文件类型：模板与示例
ALLOWED_SUFFIX = {".example", ".md"}


def test_gitignore_exists_and_covers_env() -> None:
    """.env 必须被忽略。"""
    assert GITIGNORE.exists(), ".gitignore 不存在，.env 会被提交"
    text = GITIGNORE.read_text(encoding="utf-8")
    lines = {ln.strip() for ln in text.splitlines()}

    assert ".env" in lines, ".gitignore 未忽略 .env"
    assert "backend/.env" in lines or "**/.env" in lines, (
        ".gitignore 未忽略 backend/.env"
    )


def test_gitignore_allows_env_example() -> None:
    """模板文件必须能提交，否则别人拿不到配置说明。"""
    text = GITIGNORE.read_text(encoding="utf-8")
    assert "!.env.example" in text, (
        ".gitignore 未放行 .env.example，别人拿到代码无法知道该配哪些变量"
    )


@pytest.mark.parametrize(
    "subpath",
    [
        "backend/app",
        "backend/scripts",
        "backend/tests",
        "frontend/src",
        "scripts",
    ],
)
def test_no_hardcoded_secret_in_source(subpath: str) -> None:
    """源码目录里不能出现形如真实密钥的字符串。"""
    base = ROOT / subpath
    if not base.exists():
        pytest.skip(f"{subpath} 不存在")

    offenders: list[str] = []
    for p in base.rglob("*"):
        if not p.is_file() or p.suffix in {".pyc", ".png", ".jpg", ".pdf", ".docx"}:
            continue
        if "__pycache__" in p.parts or "node_modules" in p.parts:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if SECRET_PATTERN.search(text):
            offenders.append(str(p.relative_to(ROOT)))

    assert not offenders, f"以下文件疑似含硬编码密钥：{offenders}"


def test_env_example_has_no_real_key() -> None:
    """模板里的 API Key 必须是占位符，不能是真值。"""
    for name in ("backend/.env.example", ".env.example"):
        f = ROOT / name
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        match = SECRET_PATTERN.search(text)
        assert not match, (
            f"{name} 含疑似真实密钥：{match.group()[:10]}…\n"
            "模板里应写空值或 your-key-here 之类的占位符"
        )


def test_jwt_secret_is_insecure_by_default() -> None:
    """JWT 默认密钥必须是明显的占位值，提示使用者必须改。"""
    from app.config import Settings

    default = Settings.model_fields["JWT_SECRET_KEY"].default
    insecure = {"change-me-in-production", "secret", "xxx", ""}
    assert default in insecure, (
        f"JWT_SECRET_KEY 默认值 {default!r} 不像占位符——"
        "上线前必须替换成随机串"
    )
