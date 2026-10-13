"""配置真实性守护测试。

背景（真实踩过的坑）：
`.env.example` 曾写着 `VECTOR_STORE=chroma`、`EMBEDDING_PROVIDER=openai`，
但代码里**没有任何一行读取这些配置**——实际用的是 PGVector + 字符 n-gram。
这种「配置说一套、代码做一套」的情况在面试时是雷：
面试官打开 .env 看到 Chroma，而简历写的是 PGVector。

更有害的是 `.env.example` 里 `EMBEDDING_PROVIDER=openai` 曾是**被真实读取的**
默认值——照着模板部署会直接卡住。

所以立两条规矩，用测试守住：
1. 模板里出现的每个键，代码里必须存在
2. 代码里定义的每个键，必须真的有人读（不允许僵尸配置）
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"

ENV_TEMPLATES = [
    BACKEND / ".env.example",
    ROOT / ".env.example",
]

# 这些键允许「只定义不使用」：
# - PROJECT_NAME/VERSION：元信息，给 /docs 用
# - DEBUG：透传给 SQLAlchemy echo，不以 settings.DEBUG 形式出现
ALLOWED_UNUSED = {
    "PROJECT_NAME",
    "VERSION",
    "DEBUG",
}


def _declared_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.add(line.split("=")[0].strip())
    return keys


@pytest.mark.parametrize("template", ENV_TEMPLATES, ids=lambda p: p.name)
def test_env_template_keys_exist_in_settings(template: Path) -> None:
    """模板里写的每个键都必须在 Settings 里定义（防拼写错误/已删项残留）。"""
    sys_path = str(BACKEND)
    import sys

    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from app.config import Settings

    fields = set(Settings.model_fields)
    declared = _declared_keys(template)
    if not declared:
        pytest.skip(f"{template.name} 不存在")

    missing = declared - fields
    assert not missing, (
        f"{template.name} 声明了代码里不存在的配置项：{sorted(missing)}"
    )


def test_no_zombie_config() -> None:
    """代码里定义的每个配置都必须有人读，不允许僵尸配置。"""
    import subprocess
    import sys

    sys_path = str(BACKEND)
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from app.config import Settings

    zombie: list[str] = []
    for key in sorted(Settings.model_fields):
        if key in ALLOWED_UNUSED:
            continue
        r = subprocess.run(
            ["grep", "-rl", f"settings.{key}", "app", "scripts"],
            cwd=BACKEND,
            capture_output=True,
            text=True,
        )
        if not r.stdout.strip():
            zombie.append(key)

    assert not zombie, (
        f"以下配置项定义了却无人读取（僵尸配置，会误导使用者）：{zombie}\n"
        "处理方式：要么让代码真的用它，要么从 Settings 中删除。"
    )


def test_no_chroma_claim() -> None:
    """项目实际用 PGVector，任何地方都不该再声称用 Chroma。"""
    targets = [
        BACKEND / "app" / "config.py",
        BACKEND / ".env.example",
        ROOT / ".env.example",
        ROOT / "docker-compose.yml",
    ]
    offenders = []
    for t in targets:
        if not t.exists():
            continue
        for i, line in enumerate(t.read_text(encoding="utf-8").splitlines(), 1):
            low = line.lower()
            # 允许在「已删除/不要用」的说明性注释里提到 chroma
            if "chroma" in low and not line.strip().startswith("#"):
                if "从未" in line or "已删除" in line:
                    continue
                offenders.append(f"{t.name}:{i}: {line.strip()[:70]}")

    assert not offenders, "仍有地方声称使用 Chroma：\n" + "\n".join(offenders)


def test_embedding_provider_default_is_char() -> None:
    """默认必须是 char——openai 需要 API Key 且要重建向量，不能作默认。"""
    import sys

    sys_path = str(BACKEND)
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from app.config import Settings

    assert Settings.model_fields["EMBEDDING_PROVIDER"].default == "char", (
        "EMBEDDING_PROVIDER 默认值必须是 char"
    )


def test_hybrid_weights_read_from_config() -> None:
    """混合检索权重必须来自配置，而非硬编码在类属性里。"""
    text = (BACKEND / "app" / "rag" / "retriever.py").read_text(encoding="utf-8")
    assert "W_VECTOR = settings." in text, "W_VECTOR 未接配置"
    assert "W_BM25 = settings." in text, "W_BM25 未接配置"
    assert "settings.RETRIEVER_POOL_MULTIPLIER" in (
        BACKEND / "app" / "services" / "retrieval_service.py"
    ).read_text(encoding="utf-8"), "候选池倍数未接配置"
