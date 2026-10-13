"""异步任务链路测试。

对应 JD「异步任务」与「可观测性」。

**必须测到的三件事**：

1. 落库逻辑只有一份 —— 同步与异步链路行为完全一致
2. 进度回调真的被触发 —— 漏 await 会静默失效
3. Worker 自己建任务记录 —— 不能依赖调用方
"""

from __future__ import annotations

import asyncio

import pytest


class TestProgressCallback:
    """进度回调是 Celery 链路的可观测基础。

    这里最大的坑是：`on_progress(agent, state)` 返回协程但没 await，
    回调触发 0 次，**不报任何错**，进度永远停在 5%。
    """

    def test_sync_callback_fires_for_every_agent(self):
        from app.agents.orchestrator import run_review

        fired: list[str] = []

        def cb(agent, state):
            fired.append(agent)

        asyncio.run(run_review("基坑开挖深度6.5m，周长320m。采用内支撑。", on_progress=cb))

        assert len(fired) >= 4, f"进度回调触发过少（{len(fired)} 次）"
        assert "document_parser" in fired
        assert "hazard_identifier" in fired
        assert "compliance_reviewer" in fired

    def test_async_callback_fires_for_every_agent(self):
        """协程回调也必须真正执行。

        这是最隐蔽的失败模式——写成 `async def cb` 但调用处忘了 await，
        回调返回一个从未被 await 的协程对象，触发次数为 0 且无异常。
        """
        from app.agents.orchestrator import run_review

        fired: list[str] = []

        async def cb(agent, state):
            fired.append(agent)

        asyncio.run(run_review("基坑开挖深度6.5m，周长320m。采用内支撑。", on_progress=cb))

        assert len(fired) >= 4, (
            f"协程回调触发 {len(fired)} 次 —— 调用处漏了 await，"
            "进度更新会静默失效"
        )

    def test_progress_failure_does_not_break_review(self):
        """进度回调报错不能影响审查主流程。

        可观测性是辅助能力，不能成为单点故障。
        """
        from app.agents.orchestrator import run_review

        def boom(agent, state):
            raise RuntimeError("数据库挂了")

        result = asyncio.run(
            run_review("基坑开挖深度6.5m，周长320m。", on_progress=boom)
        )
        assert result["is_hazard_project"] is True
        assert len(result["review_items"]) >= 5

    def test_no_callback_is_fine(self):
        """不传回调（同步链路）必须正常工作。"""
        from app.agents.orchestrator import run_review

        result = asyncio.run(run_review("基坑开挖深度6.5m，周长320m。"))
        assert result["is_hazard_project"] is True


class TestCeleryConfig:
    """Celery 配置错误往往静默——任务投出去没人消费。"""

    def test_tasks_are_registered_explicitly(self):
        """必须用 include= 显式声明任务。

        只靠 autodiscover_tasks 实测 `[tasks]` 列表是空的，
        消息会一直挂在 Redis 队列里没人处理，且不报错。
        """
        from app.celery_app import celery_app

        assert "app.tasks.review_tasks" in (celery_app.conf.include or ()), (
            "任务模块未显式注册，Worker 启动后不会消费这些任务"
        )

    def test_review_task_is_importable_and_named(self):
        from app.tasks.review_tasks import run_review_task

        assert run_review_task.name == "app.tasks.review_tasks.run_review_task"

    def test_long_document_timeout(self):
        """大文档审查可能几分钟，不能用默认 180 秒超时。"""
        from app.celery_app import celery_app

        assert celery_app.conf.task_time_limit >= 900, (
            f"超时过短（{celery_app.conf.task_time_limit}s），"
            "大文档审查会被中途杀掉"
        )

    def test_acks_late_for_crash_recovery(self):
        """Worker 崩溃时任务必须重回队列而不是丢失。"""
        from app.celery_app import celery_app

        assert celery_app.conf.task_acks_late is True

    def test_json_only_serialization(self):
        """只允许 JSON，避免 pickle 反序列化的执行风险。"""
        from app.celery_app import celery_app

        assert celery_app.conf.task_serializer == "json"
        assert celery_app.conf.accept_content == ["json"]


class TestStageProgress:
    """进度百分比必须单调递增，不能倒退。"""

    def test_stage_percentages_are_ordered(self):
        from app.tasks.review_tasks import STAGE_PROGRESS

        pcts = [p for _, p in STAGE_PROGRESS]
        assert pcts == sorted(pcts), "阶段进度必须单调递增"
        assert pcts[-1] <= 100

    def test_percent_lookup_covers_known_agents(self):
        from app.tasks.review_tasks import STAGE_PROGRESS, STAGE_LABELS, _percent_for

        for stage, _ in STAGE_PROGRESS:
            assert _percent_for(f"ComplianceReviewerAgent_{stage}") > 0
            assert stage in STAGE_LABELS, f"{stage} 缺少中文标签"


class TestSharedPersistence:
    """同步与异步必须共用同一个落库函数。

    各写一份的话，几周后必然出现
    「同步版本修了 bug、异步版本忘了改」这种极难排查的不一致。
    """

    def test_interface_uses_shared_persistence(self):
        import inspect

        from app.api.v1 import reviews as mod
        from app.services.review_persistence import persist_review_result

        src = inspect.getsource(mod.create_review)
        assert "persist_review_result" in src, (
            "审查接口未使用共享落库函数"
        )
        assert inspect.iscoroutinefunction(persist_review_result)

    def test_task_uses_shared_persistence(self):
        import inspect

        from app.services.review_persistence import persist_review_result
        from app.tasks import review_tasks

        src = inspect.getsource(review_tasks._run_task)
        assert "persist_review_result" in src, (
            "Celery 任务未使用共享落库函数"
        )

    def test_async_task_record_has_current_stage(self):
        """async_tasks 必须有 current_stage 字段。

        这个字段原本不存在，报
        TypeError: 'current_stage' is an invalid keyword argument。
        """
        from app.models.chat import AsyncTask

        assert hasattr(AsyncTask, "current_stage")

    def test_review_children_have_audit_field(self):
        """审查意见与论证要点必须能追溯来源。

        没有 created_by 就无法回答「这条意见是谁产生的」。
        """
        from app.models.review import ExpertPoint, ReviewItem

        assert hasattr(ReviewItem, "created_by")
        assert hasattr(ExpertPoint, "created_by")