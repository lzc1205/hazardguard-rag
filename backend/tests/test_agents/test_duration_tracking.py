"""审查耗时统计的回归测试。

背景（真实踩过的坑）：
`persist_review_result()` 在审查**跑完之后**才落库，却用同一个 `now`
同时赋给 `started_at` 和 `completed_at`，导致统计页的「平均耗时」
永远是 0.00 秒。这会让人误以为审查根本没调用 LLM——
而实际上 token 消耗 8000+、成本 ¥0.017，是真调了。

这类 bug 靠肉眼看界面发现不了（数字看起来"合理地快"），
必须用断言把「耗时应大于 0 且与墙钟耗时接近」钉死。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from app.agents.orchestrator import ReviewOrchestrator, run_review
from app.services.review_persistence import persist_review_result


SAMPLE_SCHEME = """深基坑工程专项施工方案
一、工程概况
本工程基坑开挖深度18.5米，周长约420米，采用钻孔灌注桩加两道钢筋混凝土内支撑支护。
本工程属于危险性较大的分部分项工程。
二、编制依据
《建筑基坑支护技术规程》JGJ 120-2012
三、施工计划
开挖分4层，总工期8个月。
四、施工工艺技术
基坑开挖遵循分层分段施工，先撑后挖。基坑监测每三天进行一次。周边堆载不控制。
五、施工安全保证措施
安排专人巡查边坡。
六、施工管理及作业人员配备和分工
专职安全员2名，作业班组45人。
七、验收要求
支护结构施工完成后由项目技术负责人组织验收。
八、应急处置措施
配备沙袋、抽水泵等应急物资。
九、计算书及相关施工图纸
附支护结构计算书及平面布置图。
"""


@pytest.mark.asyncio
async def test_orchestrator_records_real_timestamps() -> None:
    """编排器结果必须带真实起止时刻，且耗时与墙钟耗时接近。"""
    wall_start = time.perf_counter()
    result = await run_review(SAMPLE_SCHEME)
    wall = time.perf_counter() - wall_start

    started = result.get("started_at")
    finished = result.get("finished_at")

    assert started is not None, "结果缺少 started_at，耗时统计会退化为 0"
    assert finished is not None, "结果缺少 finished_at，耗时统计会退化为 0"

    # 落库耗时不应为 0——这是本测试要守住的核心断言
    elapsed = (finished - started).total_seconds()
    assert elapsed > 0, f"落库耗时为 {elapsed}，说明起止时刻被赋成了同一个值"

    # 与真实墙钟耗时同量级（允许编排外开销，不要求严格相等）
    assert elapsed <= wall + 1.0, (
        f"落库耗时 {elapsed:.2f}s 大于墙钟耗时 {wall:.2f}s，时间戳逻辑有误"
    )
    assert elapsed >= wall * 0.5, (
        f"落库耗时 {elapsed:.2f}s 明显小于墙钟耗时 {wall:.2f}s，起点取得太晚"
    )


@pytest.mark.asyncio
async def test_persist_uses_result_timestamps(db_ready) -> None:
    """落库必须采用结果里的时间戳，而不是自己取 now。

    **用测试库（hazardguard_test）而不是生产库**：这里会真的写入记录，
    连生产库会污染用户的真实审查数据——尤其 `completed_at`
    若被写成 now，所有历史记录的耗时统计都会被这次测试带偏。
    """
    result = await run_review(SAMPLE_SCHEME)

    # 构造一个明显早于 now 的起点，验证落库原样保留
    base = datetime.now(timezone.utc).replace(microsecond=0)
    result["started_at"] = base
    result["finished_at"] = base + timedelta(seconds=42)

    from sqlalchemy import select

    from app.models.review import Review

    db = db_ready
    review = await persist_review_result(
        db,
        result=result,
        project_id=None,
        document_id=None,
        title="耗时落库验证",
        review_type="auto",
        user_id=None,
        status="completed",
    )
    await db.commit()

    row = (
        await db.execute(select(Review).where(Review.id == review.id))
    ).scalar_one()

    assert row.started_at is not None and row.completed_at is not None
    dur = (row.completed_at - row.started_at).total_seconds()
    assert dur == pytest.approx(42.0, abs=1.0), (
        f"落库耗时应为 42 秒，实际 {dur}——说明时间戳被覆盖了"
    )

    # 物理删除：这是测试库，标记软删除会留下无用数据
    await db.delete(row)
    await db.commit()


def test_orchestrator_initializes_start_time() -> None:
    """构造编排器时就应记录起点，而不是等到落库才补。"""
    orch = ReviewOrchestrator()
    assert orch._started_at is not None
    assert orch._finished_at is None, "构造函数里不该已有结束时刻"

    delta = (datetime.now(timezone.utc) - orch._started_at).total_seconds()
    assert 0 <= delta < 60, f"起点时间异常：{delta}s"
