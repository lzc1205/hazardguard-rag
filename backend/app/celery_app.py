"""Celery 应用配置。

对应 JD「异步任务」。

**关于任务发现机制**：
原来只用 `autodiscover_tasks(["app.tasks"])`，
实测启动时 `[tasks]` 列表是空的 —— 任务没被注册，
投递出去的消息会一直挂在 Redis 队列里没人消费，
而且不报任何错，只能靠等消息超时才发现。

autodiscover 依赖 celery 在 worker 启动时动态 import，
在 Windows + 相对导入的场景下容易静默失败。
所以改成 `include=` 显式声明：worker 启动时就完成注册，
`celery -A app inspect registered` 能直接看到任务列表。

这类配置错误必须能从启动日志一眼看出来，不能靠排查业务问题才发现。
"""

from celery import Celery

from app.config import settings

celery_app = Celery(
    "hazardguard",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.review_tasks"],
)

celery_app.conf.update(
    # ---- 序列化：只用 JSON，避免 pickle 反序列化的执行风险
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # ---- 时区
    timezone="Asia/Shanghai",
    enable_utc=True,
    # ---- 可靠性
    task_track_started=True,
    # Worker 崩溃时任务重回队列而不是丢失
    task_acks_late=True,
    # 任务执行慢时不要预取堆积，保证公平调度
    worker_prefetch_multiplier=1,
    worker_healthcheck_interval=30,
    # ---- 超时：大文档审查可能几分钟，不能用默认 180 秒
    task_time_limit=1800,
    task_soft_time_limit=1500,
    result_expires=86400,
)

celery_app.autodiscover_tasks(["app.tasks"], related_name="tasks")