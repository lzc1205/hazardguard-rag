"""Prompt 版本管理与持久化。

对应 JD「Prompt Engineering 47%」中的工程化部分。

**为什么要存数据库而不是只写代码里**：
Prompt 是需要迭代的——改一版就要跑评测对比效果。
存进 `prompt_versions` 表后可以：
1. 记录每次改动的 changelog，知道为什么改
2. 保存评测分数，证明「迭代有效」而不是凭感觉改
3. 灰度切换：新版本先在部分场景验证

**Prompt Engineering 的分水岭就在这**——
把 Prompt 当字符串拼接的，是调用 API；把 Prompt 当资产管理并量化效果的，才是工程。
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 允许直接 `python app/agents/prompt_manager.py` 运行
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts.templates import PROMPT_REGISTRY, get_prompt
from app.models.chat import PromptVersion


async def sync_prompts_to_db(session: AsyncSession, force: bool = False) -> dict:
    """把代码中的 Prompt 模板同步到数据库。

    幂等：已存在的 name+version 不重复写入，除非 force=True。
    """
    created = updated = skipped = 0

    for name, meta in PROMPT_REGISTRY.items():
        existing = (
            await session.execute(
                select(PromptVersion).where(
                    PromptVersion.name == name,
                    PromptVersion.version == meta["version"],
                    PromptVersion.is_deleted == False,
                )
            )
        ).scalar_one_or_none()

        if existing:
            if force and existing.template != meta["template"]:
                existing.template = meta["template"]
                existing.changelog = meta["changelog"]
                existing.variables = meta["variables"]
                updated += 1
            else:
                skipped += 1
            continue

        session.add(
            PromptVersion(
                name=name,
                version=meta["version"],
                template=meta["template"],
                variables=meta["variables"],
                changelog=meta["changelog"],
                is_active=True,
            )
        )
        created += 1

    await session.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


async def list_versions(session: AsyncSession, name: str | None = None) -> list[dict]:
    """列出 Prompt 版本（按名称倒序，最新在前）。"""
    query = select(PromptVersion).where(PromptVersion.is_deleted == False)
    if name:
        query = query.where(PromptVersion.name == name)

    rows = (
        (await session.execute(query.order_by(PromptVersion.name, PromptVersion.version)))
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "version": r.version,
            "changelog": r.changelog,
            "variables": r.variables or [],
            "is_active": bool(r.is_active),
            "eval_score": r.eval_score,
            "template_length": len(r.template or ""),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


async def activate_version(
    session: AsyncSession, name: str, version: str
) -> dict:
    """启用指定版本（同一 name 下只有一版处于激活态）。

    这是 Prompt 灰度的基础：先在新版本上验证评测分数，
    确认不劣于旧版再切active。
    """
    target = (
        await session.execute(
            select(PromptVersion).where(
                PromptVersion.name == name,
                PromptVersion.version == version,
                PromptVersion.is_deleted == False,
            )
        )
    ).scalar_one_or_none()

    if target is None:
        raise ValueError(f"Prompt 版本不存在：{name}@{version}")

    others = (
        (
            await session.execute(
                select(PromptVersion).where(
                    PromptVersion.name == name, PromptVersion.is_deleted == False
                )
            )
        )
        .scalars()
        .all()
    )
    for r in others:
        r.is_active = r.id == target.id

    await session.commit()
    return {"name": name, "active_version": version}


async def record_eval(
    session: AsyncSession,
    name: str,
    version: str,
    metrics: dict[str, float],
) -> dict:
    """记录某版 Prompt 的评测结果。

    **这是「Prompt 迭代有效」的唯一证据来源**——
    没有评测分数的 Prompt 优化只是个人感觉，无法在面试中量化陈述。
    """
    row = (
        await session.execute(
            select(PromptVersion).where(
                PromptVersion.name == name, PromptVersion.version == version
            )
        )
    ).scalar_one_or_none()

    if row is None:
        raise ValueError(f"Prompt 版本不存在：{name}@{version}")

    # 保留历史最高分，便于对比迭代效果
    history = dict(row.eval_score or {})
    history.setdefault("history", []).append(
        {"at": datetime.now(timezone.utc).isoformat(), **metrics}
    )
    row.eval_score = {**metrics, "history": history["history"][-5:]}

    await session.commit()
    return {"name": name, "version": version, "eval_score": metrics}


def get_active_prompt(session: AsyncSession, name: str) -> dict:
    """同步版本：从内存注册表取当前启用版本。

    高频调用路径不查库——每次渲染都查一次会给数据库平白增加压力。
    版本切换通过 sync_prompts_to_db 落库，运行时以代码为准。
    """
    return get_prompt(name)


async def main() -> None:
    from app.database import async_session

    async with async_session() as session:
        result = await sync_prompts_to_db(session, force=True)
        print(
            f"Prompt 同步完成：新增 {result['created']}，"
            f"更新 {result['updated']}，跳过 {result['skipped']}"
        )

        versions = await list_versions(session)
        print(f"\n数据库中共有 {len(versions)} 个版本：")
        for v in versions:
            print(f"  {v['name']:<24} v{v['version']:<6} "
                  f"{v['template_length']:>5} 字符  变量 {len(v['variables'])} 个")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())