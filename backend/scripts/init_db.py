"""一键初始化：建表 → 管理员账号 → 法规种子 → 向量索引。

为什么需要它：
之前项目**没有任何自动初始化逻辑**——数据库里的 admin 账号和
23 条法规种子数据都是早期手工插入的。别人拿到这份代码，
按 README 启动后会在登录页卡住：提示「用户名或密码错误」，
但其实数据库里根本没有用户。这是「代码能跑但用不了」的典型。

本脚本幂等，可重复执行：
- 建表用 Alembic 之外再做一次 create_all 兜底（compose 里已跑迁移，
  这里保证裸机用 SQLite/本地 PG 也能起）
- 管理员已存在则跳过，不覆盖已有密码
- 种子数据按 (规范号, 条款号) 判重
- 向量索引只在缺失时构建

用法：
    cd D:\\123456789\\PythonProject6\\backend
    ..\\.venv\\Scripts\\python.exe scripts\\init_db.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import func, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import async_session, engine  # noqa: E402
from app.models.base import BaseModel  # noqa: E402
from app.models.user import User  # noqa: E402

# 必须导入全部模型，否则 BaseModel.metadata 里只有 User，
# create_all 只会建出 users 一张表。
from app.models import chat as _chat  # noqa: E402,F401
from app.models import knowledge as _knowledge  # noqa: E402,F401
from app.models import project as _project  # noqa: E402,F401
from app.models import review as _review  # noqa: E402,F401


async def ensure_vector_extension() -> None:
    """确保 pgvector 扩展存在（幂等）。

    **为什么必须显式创建**：knowledge_chunks.embedding 是 VECTOR(512) 列，
    该类型由 pgvector 扩展提供。扩展不存在时建表直接失败：
    `type "vector" does not exist`。
    Alembic 迁移里有这一步，但手动建库（测试库 / 裸机部署）不会走迁移，
    于是建表就炸了。这里补上，让建库不依赖 Alembic 是否执行过。
    """
    from sqlalchemy import text

    async with engine.begin() as conn:
        exists = (
            await conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            )
        ).scalar_one_or_none()
        if exists:
            print("[1/5] pgvector 扩展已存在")
            return
        # 镜像 pgvector/pgvector 已内置扩展文件，无需下载编译
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    print("[1/5] pgvector 扩展已创建")


async def create_tables() -> None:
    """建表（幂等）。

    compose 启动时已跑 `alembic upgrade head`，这里是兜底：
    裸机部署或迁移文件缺失时，至少保证表存在。
    """
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    print("[2/5] 数据表已就绪")


async def ensure_admin() -> None:
    """创建初始管理员（幂等，不覆盖已有密码）。"""
    username = settings.DEFAULT_ADMIN_USERNAME
    password = settings.DEFAULT_ADMIN_PASSWORD

    async with async_session() as db:
        exists = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()

        if exists:
            print(f"[3/5] 管理员 '{username}' 已存在，跳过")
            return

        from app.utils.security import hash_password

        db.add(
            User(
                username=username,
                email=None,
                hashed_password=hash_password(password),
                role="admin",
                full_name="系统管理员",
                is_active=True,
            )
        )
        await db.commit()
        print(f"[3/5] 管理员已创建：{username} / {password}")


async def seed_knowledge() -> dict:
    """导入法规种子数据（幂等）。"""
    from app.data.seed import seed_knowledge_entries

    async with async_session() as db:
        result = await seed_knowledge_entries(db)
        print(
            f"[4/5] 法规种子：新增 {result.get('created', 0)} 条，"
            f"总计 {result.get('total', '?')} 条"
        )
        return result


async def build_vectors() -> None:
    """构建向量索引（仅在缺失时）。"""
    from app.rag.vector_store import PgVectorStore

    async with async_session() as db:
        store = PgVectorStore(db)
        stats = await store.stats()
        total = stats.get("total_chunks", 0)
        indexed = stats.get("indexed_chunks", 0)

        if total and indexed >= total:
            print(f"[5/5] 向量索引已完整（{indexed}/{total}），跳过")
            return

        from app.rag.vector_store import index_seed_knowledge

        result = await index_seed_knowledge(db)
        print(
            f"[5/5] 向量索引：{result.get('chunks_indexed', 0)} 条，"
            f"维度 {result.get('dimension', '?')}"
        )


async def main() -> int:
    print("=" * 56)
    print("HazardGuard-RAG 初始化")
    print("=" * 56)

    try:
        # 顺序有依赖：扩展必须先于建表，否则 VECTOR(512) 列建不出来
        await ensure_vector_extension()
        await create_tables()
        await ensure_admin()
        await seed_knowledge()
        await build_vectors()
    except Exception as exc:  # noqa: BLE001
        print(f"\n初始化失败：{type(exc).__name__}: {exc}")
        print("\n排查顺序：")
        print("  1. 数据库是否已启动？docker ps | grep hazardguard")
        print("  2. .env 里的 DATABASE_URL 是否正确？")
        print("  3. 端口 5432 是否被其他 PostgreSQL 占用？")
        return 1

    print("\n" + "=" * 56)
    print("初始化完成，可以启动服务了：")
    print("  python scripts\\start.py --restart")
    print(f"  登录账号：{settings.DEFAULT_ADMIN_USERNAME} / {settings.DEFAULT_ADMIN_PASSWORD}")
    print("=" * 56)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
