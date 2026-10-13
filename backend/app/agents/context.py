"""上下文工程与 Token 预算管理。

对应 JD 频次 21%。小红书 JD 原文：
「负责多轮对话与模型交互的上下文工程，包括消息构造、历史筛选、
Token 预算、上下文裁剪与压缩、状态隔离、短期与长期记忆，
以及 RAG 检索结果的组织与回填」

核心原则：**超预算必须降级，不能中断**。
审查流程跑到一半抛异常，前面的 token 全白花了——这是最差的行为。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------- Token 估算


def estimate_tokens(text: str) -> int:
    """估算 token 数。

    中文约 1 字≈1 token，英文约 4 字符≈1 token。
    不引入 tiktoken 依赖——估算精度足够做预算控制，
    而精确计数会拖慢每次调用的开销。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return int(cjk + other / 4) + 1


# ---------------------------------------------------------------- 预算管理


@dataclass
class TokenBudget:
    """单次审查的全局 token 预算。

    按环节分配而非平均切分——危大识别只需几百 token，
    合规审查才是大头，预算错配会导致前者浪费、后者不够。
    """

    total: int = 100_000
    by_stage: dict[str, int] = field(
        default_factory=lambda: {
            "document_parse": 15_000,
            "hazard_identify": 5_000,
            "regulation_retrieval": 10_000,
            "compliance_review": 40_000,
            "expert_argumentation": 20_000,
            "report_generate": 10_000,
        }
    )
    consumed: dict[str, int] = field(default_factory=dict)

    def remaining(self, stage: str) -> int:
        """某环节剩余可用额度。"""
        limit = self.by_stage.get(stage, 0)
        used = self.consumed.get(stage, 0)
        return max(limit - used, 0)

    def total_consumed(self) -> int:
        return sum(self.consumed.values())

    def is_exhausted(self, stage: str) -> bool:
        return self.remaining(stage) <= 0

    def consume(self, stage: str, tokens: int) -> None:
        self.consumed[stage] = self.consumed.get(stage, 0) + tokens

    def usage_report(self) -> dict[str, Any]:
        rows = []
        for stage, limit in self.by_stage.items():
            used = self.consumed.get(stage, 0)
            rows.append(
                {
                    "stage": stage,
                    "limit": limit,
                    "used": used,
                    "usage_pct": round(used / limit * 100, 1) if limit else 0.0,
                }
            )
        return {
            "total_limit": self.total,
            "total_used": self.total_consumed(),
            "by_stage": rows,
        }


@dataclass
class DegradationPolicy:
    """超预算降级策略。

    按「代价小→ 代价大」排序，逐级触发而不是一步到位。
    """

    strategies: list[str] = field(
        default_factory=lambda: [
            "reduce_recall_k",      # 先降召回数量，最省
            "compress_context",     # 再压上下文
            "switch_model_down",    # 再降模型
            "mark_degraded",        # 最后标记降级执行，但不中断
        ]
    )
    applied: list[str] = field(default_factory=list)

    def next_strategy(self) -> str | None:
        for s in self.strategies:
            if s not in self.applied:
                self.applied.append(s)
                return s
        return None

    @property
    def degraded(self) -> bool:
        return bool(self.applied)


class ContextBudgetGuard:
    """把预算与降级策略组合成一个调用入口。"""

    def __init__(self, budget: TokenBudget | None = None):
        self.budget = budget or TokenBudget()
        self.policy = DegradationPolicy()

    def check(self, stage: str, planned_tokens: int) -> dict[str, Any]:
        """调用 LLM 前检查预算，返回执行计划。"""
        remaining = self.budget.remaining(stage)
        plan: dict[str, Any] = {
            "stage": stage,
            "planned": planned_tokens,
            "remaining": remaining,
            "degraded": False,
            "actions": [],
        }

        if planned_tokens <= remaining:
            return plan

        # 需要降级
        plan["degraded"] = True
        while planned_tokens > self.budget.remaining(stage):
            strategy = self.policy.next_strategy()
            if strategy is None:
                break
            plan["actions"].append(strategy)
            if strategy == "reduce_recall_k":
                # 降级为直接标记执行，由调用方缩减召回
                plan["recall_k"] = 5
            elif strategy == "compress_context":
                plan["compress"] = True
            elif strategy == "switch_model_down":
                plan["model"] = "降级模型"
            elif strategy == "mark_degraded":
                plan["note"] = "已标记降级执行，流程继续但结果需人工复核"
                break

        return plan

    def record(self, stage: str, tokens: int) -> None:
        self.budget.consume(stage, tokens)


# ---------------------------------------------------------------- 上下文压缩


class ContextCompressor:
    """上下文裁剪与压缩。"""

    def __init__(self, max_tokens: int = 4000, keep_quote_chars: int = 200):
        self.max_tokens = max_tokens
        self.keep_quote_chars = keep_quote_chars

    def compress_tool_output(self, text: str) -> str:
        """工具输出裁剪：保留头尾，中间标注省略。

        多数工具输出的结论在首尾（错误信息在末尾），中间可舍。
        """
        if estimate_tokens(text) <= self.max_tokens:
            return text
        head = text[: self.keep_quote_chars]
        tail = text[-self.keep_quote_chars:]
        return f"{head}\n\n... [中间内容已省略，共 {len(text)} 字符] ...\n\n{tail}"

    def compress_history(self, messages: list[dict], keep_recent: int = 3) -> list[dict]:
        """历史消息滚动压缩：保留最近 N 轮原文，更早的合并为摘要。"""
        if len(messages) <= keep_recent:
            return messages

        older = messages[:-keep_recent]
        recent = messages[-keep_recent:]

        topics = []
        for m in older:
            if m.get("role") == "user":
                text = (m.get("content") or "")[:40]
                topics.append(text)

        summary = {
            "role": "system",
            "content": (
                f"[历史对话摘要]此前共 {len(older)} 轮对话，"
                f"用户主要关注：{'；'.join(topics[:5])}"
                if topics
                else f"[历史对话摘要]此前共 {len(older)} 轮对话"
            ),
        }
        return [summary, *recent]

    def dedupe_chunks(self, chunks: list[dict], threshold: float = 0.9) -> list[dict]:
        """去重：同一条文多次出现时只保留一份，合并引用次数。

        简单实现用条款号判断，比向量相似度更精确且更快。
        """
        seen: dict[str, dict] = {}
        result = []
        for c in chunks:
            key = c.get("clause_no") or c.get("content", "")[:50]
            if key in seen:
                seen[key]["mention_count"] = seen[key].get("mention_count", 1) + 1
                continue
            c = {**c, "mention_count": 1}
            seen[key] = c
            result.append(c)
        return result

    def organize_retrieved(
        self, chunks: list[dict], max_tokens: int | None = None
    ) -> str:
        """检索结果组织与回填。

        不把Top-K 原文直接拼进 prompt——那会浪费大量 token 且稀释重点。
        做法：保留最强的 2 条原文，其余压缩为「规范号+条号+摘要」。
        """
        limit = max_tokens or self.max_tokens
        chunks = self.dedupe_chunks(chunks)

        parts: list[str] = []
        used = 0
        for i, c in enumerate(chunks):
            citation = c.get("citation") or f"{c.get('regulation_code', '')} {c.get('clause_no', '')}"
            if i < 2:
                block = f"【依据{i + 1}】{citation}\n{c.get('content', '')}"
            else:
                snippet = c.get("content", "")[:120]
                block = f"【参考{i + 1}】{citation}（要点：{snippet}…）"

            tokens = estimate_tokens(block)
            if used + tokens > limit:
                parts.append(f"... [另有 {len(chunks) - i} 条依据因上下文预算未展开]")
                break
            parts.append(block)
            used += tokens

        return "\n\n".join(parts)


# ---------------------------------------------------------------- 记忆


class MemoryStore:
    """短期会话记忆 + 长期偏好记忆。

    长期记忆的召回必须设相似度阈值，否则不相关的历史会污染上下文——
    这比没有记忆更糟。
    """

    def __init__(self, long_term_threshold: float = 0.7, max_long_term: int = 3):
        self.long_term_threshold = long_term_threshold
        self.max_long_term = max_long_term
        self.short_term: list[dict] = []
        self.long_term: list[dict] = []

    def add_message(self, role: str, content: str, sources: list | None = None) -> None:
        self.short_term.append(
            {"role": role, "content": content, "sources": sources or [], "tokens": estimate_tokens(content)}
        )

    def add_preference(self, key: str, value: Any) -> None:
        self.long_term.append({"key": key, "value": value})

    def get_history(self, max_tokens: int = 2000) -> list[dict]:
        """按预算裁剪历史，从最旧开始丢弃。"""
        result: list[dict] = []
        total = 0
        for msg in reversed(self.short_term):
            t = msg["tokens"]
            if total + t > max_tokens:
                break
            result.insert(0, msg)
            total += t
        return result

    def recall_preferences(self, query: str) -> list[dict]:
        """召回相关长期记忆，超阈值才注入。"""
        if not self.long_term:
            return []
        hits = []
        for item in self.long_term:
            # 关键词重合度作为相似度近似
            q = set(query)
            v = set(str(item["key"]) + str(item["value"]))
            sim = len(q & v) / max(len(q | v), 1)
            if sim >= self.long_term_threshold:
                hits.append({**item, "similarity": round(sim, 3)})
        hits.sort(key=lambda x: x["similarity"], reverse=True)
        return hits[: self.max_long_term]

    def clear(self) -> None:
        self.short_term.clear()

    def summary(self) -> dict:
        return {
            "short_term_count": len(self.short_term),
            "short_term_tokens": sum(m["tokens"] for m in self.short_term),
            "long_term_count": len(self.long_term),
        }


# ---------------------------------------------------------------- 状态隔离


@dataclass
class ReviewContext:
    """单次审查的隔离上下文。

    每个请求独立创建，绝不跨请求共享——
    多用户并发时串上下文是不可接受的隐私事故。
    """

    review_id: str
    user_id: str | None = None
    project_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    memory: MemoryStore = field(default_factory=MemoryStore)
    budget: TokenBudget = field(default_factory=TokenBudget)
    compressor: ContextCompressor = field(default_factory=ContextCompressor)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def token_usage(self) -> dict:
        return self.budget.usage_report()


def strip_injection(text: str) -> str:
    """清理用户输入中的提示注入痕迹。

    上传的方案文档是用户可控内容，其中可能包含
    「忽略之前的指令」这类注入尝试——虽不能完全防住，但应做基础过滤。
    """
    patterns = [
        r"(?i)ignore\s+(all\s+)?(previous|above)\s+instructions?",
        r"(?i)disregard\s+(all\s+)?previous",
        r"忽略(掉)?(之前|以上|前面)的?(所有)?指令",
        r"你不需要?(再)?(遵守|执行)规则",
        r"(?i)you\s+are\s+now\s+\w+",
    ]
    cleaned = text
    for p in patterns:
        cleaned = re.sub(p, "[已过滤的注入内容]", cleaned)
    return cleaned