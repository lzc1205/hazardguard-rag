"""LangChain 集成层。

对应 JD「LangChain 31%」（校招 22% / 社招 48%，社招几乎是硬要求）。

**为什么用 LangChain 而不是全手写**：
不是为用而用，而是它解决了三个具体问题：
1. **LCEL 链式组合** —— `prompt | llm | parser` 声明式编排，
   比层层传参更清晰，且中间步骤天然可观测（LangSmith 生态）
2. **工具抽象** —— `@tool` 装饰器把普通函数变成模型可调用的工具，
   标准统一，新增工具零成本接入
3. **输出解析器** —— 结构化输出不用手写正则

**与 app/llm.py 的关系**：
app/llm.py 是裸 httpx 调用（可控、无额外依赖），
本文件在需要链式编排与工具抽象时用 LangChain。
两者共存：低层调用走 httpx，编排层用 LCEL，避免把框架绑死在核心链路。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.output_parsers import StrOutputParser  # noqa: F401  保留供外部使用
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda

from app.llm import LLMClient


# ---------------------------------------------------------------- ChatModel 适配


class LangChainChatAdapter(Runnable):
    """把 app/llm.LLMClient 适配成 LangChain 的 Runnable 接口。

    不用 `langchain_openai.ChatOpenAI` 的原因：
    1. 不想让核心链路强依赖框架版本（LangChain 1.x API 变动频繁）
    2. DeepSeek 的 base_url 与 OpenAI 完全兼容，直接调更可控
    3. 无 Key 时能精确降级，不必等框架抛异常

    实现 Runnable 的 invoke/ainvoke 即可在 LCEL 链里当节点用。
    """

    def __init__(self, client: LLMClient | None = None, **kwargs: Any):
        self.client = client or LLMClient()
        self.kwargs = kwargs

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> str:
        """同步调用。LCEL 中若混入同步节点会走这里。"""
        import asyncio

        return asyncio.run(self.ainvoke(input, config, **kwargs))

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> str:
        """异步调用——实际链路走这个。"""
        messages = self._to_messages(input)
        resp = await self.client.chat(messages, **self.kwargs)
        if not resp.success:
            raise RuntimeError(f"LLM 调用失败：{resp.error}")
        return resp.content

    @staticmethod
    def _to_messages(input: Any) -> list[dict]:
        """把 LCEL 的输入归一化为 messages 列表。"""
        if isinstance(input, str):
            return [{"role": "user", "content": input}]
        if isinstance(input, dict):
            if "role" in input and "content" in input:
                return [input]
            # 提示词变量已填充，退化为单条 user 消息
            return [{"role": "user", "content": str(input)}]
        if isinstance(input, list):
            return [
                m if isinstance(m, dict) and "role" in m
                else {"role": "user", "content": str(m)}
                for m in input
            ]
        return [{"role": "user", "content": str(input)}]


def build_chat_model(temperature: float = 0.1, model: str | None = None) -> LangChainChatAdapter:
    """构造 LangChain 聊天模型节点。"""
    client = LLMClient(model=model) if model else LLMClient()
    return LangChainChatAdapter(client, temperature=temperature)


# ---------------------------------------------------------------- LCEL 链


def _to_text(v: Any) -> str:
    """把 LangChain 的输出对象转成纯字符串。

    PromptTemplate 输出的是 StringPromptValue 而非 str，
    直接 str() 会得到 "text='...'" 这样的包装表示，必须调 to_string()。
    """
    if isinstance(v, str):
        return v
    to_string = getattr(v, "to_string", None)
    if callable(to_string):
        return to_string()
    return str(v)


def build_review_chain(temperature: float = 0.1) -> Runnable:
    """审查说理链：变量填充 → LLM → 字符串解析。

    **必须用 ChatPromptTemplate 而非 PromptTemplate**：
    后者把角色说明、任务、约束、输入全塞进一条 user 消息，
    模型分不清「哪段是指令、哪段是待审材料」，
    实测会把审查意见本身当成待审文本重新点评一遍。
    ChatPromptTemplate 用 system 承载指令与硬约束，human 承载待审数据，
    角色边界清晰。
    """
    from app.agents.prompts.templates import COMPLIANCE_REVIEW_V1

    # 切出约束部分（system）与数据部分（human）
    parts = COMPLIANCE_REVIEW_V1.split("## 待审查内容")
    system_part = parts[0].strip()
    human_part = parts[1].strip() if len(parts) > 1 else "{scheme_content}"

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_part),
            ("human", human_part),
        ]
    )
    return (
        prompt
        | RunnableLambda(_to_messages_for_model)
        | build_chat_model(temperature)
        | RunnableLambda(_to_text)
    )


def _to_messages_for_model(prompt_value: Any) -> list[dict]:
    """ChatPromptValue → OpenAI messages 列表。

    ChatPromptValue.to_messages() 返回的是 BaseMessage 对象，
    LLMClient 只接受 dict，需转换。
    """
    msgs = prompt_value.to_messages() if hasattr(prompt_value, "to_messages") else []
    out = []
    for m in msgs:
        role = getattr(m, "type", None) or getattr(m, "role", "user")
        # LangChain 用 "human"/"ai"，OpenAI 用 "user"/"assistant"
        role = {"human": "user", "ai": "assistant", "system": "system"}.get(role, role)
        content = getattr(m, "content", str(m))
        out.append({"role": role, "content": content})
    return out


def build_qa_chain(temperature: float = 0.1) -> Runnable:
    """法规问答链：上下文填充 → LLM → 解析。

    用 ChatPromptTemplate 而非 PromptTemplate，因为问答需要
    system 角色承载「只能依据给定资料」这条硬约束。
    """
    from app.agents.prompts.templates import QA_V1

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是建设工程法规问答助手。只依据用户提供的【参考资料】作答，"
                "资料不足时必须回答「知识库中未收录相关条款，无法给出可靠依据」。"
                "每个结论必须标注来源条款号。",
            ),
            ("human", QA_V1),
        ]
    )
    return (
        prompt
        | RunnableLambda(_to_messages_for_model)
        | build_chat_model(temperature)
        | RunnableLambda(_to_text)
    )


# ---------------------------------------------------------------- 工具抽象


class LangChainToolRegistry:
    """LangChain 工具注册表。

    把普通函数包装成标准 Tool，让它们能同时被 ReAct 与 LCEL 链使用——
    同一份工具定义，两种编排方式，不必维护两套。
    """

    def __init__(self):
        self._tools: dict[str, Any] = {}

    def register(
        self,
        name: str,
        description: str,
        func: Callable,
        args_schema: dict[str, str] | None = None,
    ) -> None:
        from langchain_core.tools import StructuredTool

        args_schema = args_schema or {}
        self._tools[name] = StructuredTool.from_function(
            func=func,
            name=name,
            description=description,
        )
        self._tools[name].__args_schema_doc__ = args_schema

    def get(self, name: str):
        return self._tools.get(name)

    def list(self) -> list[dict]:
        """按 OpenAI function-calling 格式导出，便于直接喂给模型。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": t.description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            k: {"type": "string", "description": v}
                            for k, v in getattr(t, "__args_schema_doc__", {}).items()
                        },
                        "required": list(getattr(t, "__args_schema_doc__", {}).keys()),
                    },
                },
            }
            for name, t in self._tools.items()
        ]

    def bind_to_chat(self, model: LangChainChatAdapter):
        """把工具绑定到聊天模型（function calling）。"""
        return model


# ---------------------------------------------------------------- Function Calling


async def call_with_tools(
    question: str,
    tools: list[dict],
    temperature: float = 0.1,
    max_rounds: int = 3,
) -> dict[str, Any]:
    """原生 function calling 循环。

    对应 JD「Function Calling / Tool Use 43%」。

    与 ReAct 的区别：这里让模型通过 `tools` 参数原生声明要调哪个工具，
    而不是输出 Action 文本。优点是模型侧的决策更可靠——
    结构化字段由 API 层解析，不依赖模型严格遵守输出格式。
    """
    client = LLMClient()
    if not client.available:
        return {"success": False, "answer": "", "tool_calls": [], "reason": "未配置 LLM_API_KEY"}

    messages: list[dict] = [{"role": "user", "content": question}]
    all_calls: list[dict] = []
    total_tokens = 0

    for _ in range(max_rounds):
        resp = await client.chat(
            messages, tools=tools, temperature=temperature, max_tokens=800
        )
        total_tokens += resp.tokens_used
        if not resp.success:
            return {
                "success": False,
                "answer": "",
                "tool_calls": all_calls,
                "reason": resp.error,
            }

        # 原生 function calling：优先用 tool_calls 字段，
        # 退化时解析文本（部分模型/代理不返回结构化字段）
        tool_calls = list(resp.tool_calls or [])
        if not tool_calls and "TOOL_CALL:" in (resp.content or ""):
            try:
                parsed = json.loads(
                    resp.content.split("TOOL_CALL:", 1)[1].strip().split("\n")[0]
                )
                tool_calls = [parsed]
            except (json.JSONDecodeError, IndexError):
                tool_calls = []

        messages.append({"role": "assistant", "content": resp.content or ""})

        if not tool_calls:
            return {
                "success": True,
                "answer": resp.content,
                "tool_calls": all_calls,
                "tokens": total_tokens,
            }

        # 执行工具
        from app.services import retrieval_service as svc

        for call in tool_calls:
            name = call.get("name")
            raw_args = call.get("arguments")
            try:
                args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
            except json.JSONDecodeError:
                args = {}

            if name == "search_regulation":
                result = svc.search(
                    query=args.get("query", question), top_k=args.get("top_k", 5)
                )
            elif name == "list_hazard_categories":
                result = svc.list_hazard_categories()
            else:
                result = {"error": f"未知工具 {name}"}

            all_calls.append({"name": name, "args": args})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"工具 {name} 返回："
                        f"{json.dumps(result, ensure_ascii=False)[:1500]}"
                    ),
                }
            )

    return {
        "success": False,
        "answer": "",
        "tool_calls": all_calls,
        "reason": f"达到最大轮次 {max_rounds}",
        "tokens": total_tokens,
    }


def default_tools_schema() -> list[dict]:
    """项目默认暴露给模型的工具（OpenAI function-calling 格式）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "search_regulation",
                "description": (
                    "检索现行有效的工程法规与规范条文。回答任何法规问题前必须先调用它查证。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索关键词或问题原文"},
                        "top_k": {"type": "integer", "description": "返回条数，默认 5"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_hazard_categories",
                "description": "列出七大危大工程类别及其判定阈值，用于判断是否属于危大工程。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]