"""pytest 全局夹具。

**为什么测试库要独立**：测试会创建/删除数据，如果连生产库
（hazardguard），一次 `pytest` 就能把真实审查记录搞脏。
约定：测试只碰 `hazardguard_test`。

**测试库自动创建**：之前 conftest 直接连 hazardguard_test，
但这个库从没被创建过——fixture 形同虚设，谁写谁踩坑。
这里自动建库并建表，省掉手动准备步骤。

**为什么不用内存 SQLite**：项目用了 PGVector 的 `VECTOR(512)` 列，
SQLite 不认识这个类型，建表直接失败。所以必须用真实 PostgreSQL。

**为什么建库用同步 fixture**：本项目没有 pytest 配置文件，
靠 `@pytest.mark.asyncio` 装饰器运行异步测试，这种模式下
session 级的 async fixture 不会被处理（会报 "requested an async
fixture ... with no plugin or hook that handled it"）。
所以建库逻辑放在同步 fixture 里，用 asyncio.run 驱动。

前置条件：PostgreSQL 起着。
    docker compose up -d postgres
未启动时相关测试会 skip 而不是 fail——环境问题不该被误判成代码问题。
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

PG_ADMIN_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
TEST_DB_NAME = "hazardguard_test"
TEST_DATABASE_URL = (
    f"postgresql+asyncpg://postgres:postgres@localhost:5432/{TEST_DB_NAME}"
)


def _prepare_db_sync() -> None:
    """建库 + 建表（同步驱动，供 session 级 fixture 调用）。"""

    async def _do() -> None:
        admin_engine = create_async_engine(PG_ADMIN_URL, echo=False)
        try:
            async with admin_engine.connect() as conn:
                exists = (
                    await conn.execute(
                        text("SELECT 1 FROM pg_database WHERE datname = :n"),
                        {"n": TEST_DB_NAME},
                    )
                ).scalar_one_or_none()
                if not exists:
                    # CREATE DATABASE 不能在事务块内执行
                    await conn.execute(text('COMMIT'))
                    await conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
        finally:
            await admin_engine.dispose()

        engine = create_async_engine(TEST_DATABASE_URL, echo=False)
        try:
            # 必须先装 pgvector 扩展：knowledge_chunks.embedding 是
            # VECTOR(512) 列，类型由该扩展提供。没有它建表直接报
            # `type "vector" does not exist`。生产库的扩展是
            # Alembic 迁移建的，测试库不走迁移，得自己装。
            async with engine.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

            # 必须导入全部模型，否则建表会漏表
            from app.models import chat as _c  # noqa: F401
            from app.models import knowledge as _k  # noqa: F401
            from app.models import project as _p  # noqa: F401
            from app.models import review as _r  # noqa: F401
            from app.models.base import BaseModel

            async with engine.begin() as conn:
                await conn.run_sync(BaseModel.metadata.create_all)
        finally:
            await engine.dispose()

    asyncio.run(_do())


@pytest.fixture(scope="session")
def test_db() -> str:
    """确保 hazardguard_test 存在且表结构完整，返回连接串。

    数据库未就绪时 skip（而非 fail）——环境问题不是代码缺陷。
    """
    try:
        _prepare_db_sync()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"测试库未就绪：{type(exc).__name__}: {exc}\n"
            "请先启动 PostgreSQL：docker compose up -d postgres"
        )
    return TEST_DATABASE_URL


@pytest.fixture
async def db_session(test_db: str) -> AsyncSession:
    """测试专用数据库会话。"""
    engine = create_async_engine(test_db, echo=False)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def db_ready(db_session: AsyncSession) -> AsyncSession:
    """确认测试库连通可用。"""
    await db_session.execute(text("SELECT 1"))
    return db_session