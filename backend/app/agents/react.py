"""ReAct Agent（Reason + Act）。

对应 JD「Agent 架构 37%」与需求文档 6.6 节。

**为什么在已有多 Agent 编排之上还要做 ReAct**：
现有的编排是**确定性流程**——危大识别、检索、审查按固定顺序执行，
每一步做什么是预先定好的。这适合审查这类**步骤明确**的任务。

但真实使用中有两类问题是固定流程处理不了的：
1. **用户问法千变万化**：「基坑要挖多深才算危大」vs
   「我这个方案要不要专家论证」——前者问阈值，后者问流程
2. **需要多轮探查才能回答**：「脚手架这个方案有问题吗」
   → 需先查脚手架规范 → 再看方案内容 → 再比对条款

ReAct 的价值是让模型**自己决定调哪个工具、查到什么程度**。

**与 LangChain 官方 ReAct 的区别**：
官方实现把工具调用完全交给模型决定，本项目加了**合规场景的硬约束**——
模型能调用的工具是白名单，检索失败必须走拒答路径而非自由发挥。
纯 ReAct 在合规审查里风险太高：模型可能「觉得」某条适用就引用它。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.llm import get_llm

# ---------------------------------------------------------------- 工具定义


@dataclass
class Tool:
    """可被 ReAct Agent 调用的工具。

    名称与描述直接进 Prompt——描述写不清，模型就会乱调。
    """

    name: str
    description: str
    func: Callable[..., Any]
    args_schema: dict[str, str] = field(default_factory=dict)

    def to_prompt_line(self) -> str:
        args = ", ".join(f"{k}: {v}" for k, v in self.args_schema.items())
        return f"- {self.name}({args})：{self.description}"


@dataclass
class ReActStep:
    """一步推理-行动的记录。"""

    thought: str
    action: str
    action_input: dict
    observation: str
    step_no: int
    tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "step": self.step_no,
            "thought": self.thought[:300],
            "action": self.action,
            "action_input": self.action_input,
            "observation": self.observation[:500],
            "tokens": self.tokens,
        }


class ReActAgent:
    """ReAct 循环：思考 → 行动 → 观察 → 直到得出答案或触达最大步数。

    **与 LangChain 官方实现的差异**：
    官方把解析交给模型输出格式，本项目用确定性解析 + 工具白名单。
    理由：合规场景下「模型说了什么」必须能追溯，不能只依赖模型自觉。
    """

    def __init__(
        self,
        tools: list[Tool],
        system_prompt: str,
        max_steps: int = 5,
        temperature: float = 0.1,
    ):
        self.tools = {t.name: t for t in tools}
        self.tool_list = tools
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.temperature = temperature
        self.total_tokens = 0

    # ------------------------------------------------------------ Prompt

    def _build_prompt(self, question: str, history: list[ReActStep]) -> str:
        tools_desc = "\n".join(t.to_prompt_line() for t in self.tool_list)

        parts = [
            f"可用工具：\n{tools_desc}",
            "",
            "你必须严格按以下格式输出，每次只输出一行：",
            "Thought: <你的思考，说明为什么要调用某个工具>",
            "Action: <工具名，必须是上面列出的一个>",
            "Action Input: <JSON 对象，如 {\"query\": \"立杆间距\"}>",
            "",
            "当你已获得足够信息时，输出：",
            "Final Answer: <你的最终答案>",
        ]

        if history:
            parts.insert(0, "已有的推理过程：\n" + "\n".join(
                f"步骤{ s.step_no} 思考：{s.thought}\n"
                f"        行动：{s.action}({json.dumps(s.action_input, ensure_ascii=False)})\n"
                f"        观察：{s.observation}"
                for s in history
            ) + "\n")

        parts.append(f"\n问题：{question}")
        return "\n".join(parts)

    # ------------------------------------------------------------ 解析

    @staticmethod
    def _parse_action(text: str) -> tuple[str, dict] | None:
        """解析模型输出。

        **取最后一个 Action 而不是第一个**：
        Thought 里可能出现「我应该先 Action: xxx 查一下」这类表述，
        正则 `re.search` 会匹配到 Thought 内的第一个，导致解析出错误工具。
        真实的 Action 行一定在 Thought 之后，所以用 findall 取末位。
        """
        thought_match = re.search(r"Thought:\s*(.+?)(?=\n(?:Action:|Final Answer:)|\Z)", text, re.S)
        thought = thought_match.group(1).strip() if thought_match else ""

        if re.search(r"Final Answer:", text):
            final = re.search(r"Final Answer:\s*(.+)", text, re.S)
            return ("__final__", {"answer": final.group(1).strip() if final else ""})

        # 所有 Action 行中取最后一个（排除 Thought 内部的）
        action_matches = re.findall(r"^\s*Action:\s*(\w+)\s*$", text, re.M)
        if not action_matches:
            # 退化：允许行尾有其他内容（如注释）
            action_matches = re.findall(r"Action:\s*(\w+)", text)
        if not action_matches:
            return None
        action = action_matches[-1].strip()

        input_match = re.search(r"Action Input:\s*(\{.*?\})", text, re.S)
        if input_match:
            try:
                args = json.loads(input_match.group(1))
            except json.JSONDecodeError:
                args = {}
        else:
            # 允许模型输出非 JSON，退化成单参数
            simple = re.search(r"Action Input:\s*(\S+)", text)
            args = {"query": simple.group(1)} if simple else {}

        return (action, {"thought": thought, **args})

    # ------------------------------------------------------------ 主循环

    async def run(self, question: str) -> dict[str, Any]:
        """执行 ReAct 循环。"""
        llm = get_llm()

        if not llm.available:
            return {
                "success": False,
                "answer": "",
                "steps": [],
                "reason": "未配置 LLM_API_KEY，ReAct 需要模型驱动工具调用",
                "tokens": 0,
            }

        history: list[ReActStep] = []
        start = time.perf_counter()

        for step_no in range(1, self.max_steps + 1):
            prompt = self._build_prompt(question, history)
            resp = await llm.complete_with_prompt(
                prompt, system=self.system_prompt,
                temperature=self.temperature, max_tokens=500,
            )
            self.total_tokens += resp.tokens_used

            if not resp.success:
                return {
                    "success": False,
                    "answer": "",
                    "steps": [s.to_dict() for s in history],
                    "reason": f"LLM 调用失败：{resp.error}",
                    "tokens": self.total_tokens,
                }

            parsed = self._parse_action(resp.content)
            if parsed is None:
                # 模型没给出可解析的格式，喂回观察让它重试
                history.append(
                    ReActStep(
                        thought="", action="__parse_error__", action_input={},
                        observation="输出格式无法解析，请严格按 Thought/Action/Action Input 格式输出",
                        step_no=step_no, tokens=resp.tokens_used,
                    )
                )
                continue

            action, args = parsed

            # 结束
            if action == "__final__":
                return {
                    "success": True,
                    "answer": args.get("answer", ""),
                    "steps": [s.to_dict() for s in history],
                    "total_steps": step_no,
                    "latency_ms": int((time.perf_counter() - start) * 1000),
                    "tokens": self.total_tokens,
                }

            # 工具白名单——不在白名单内的调用一律拒绝
            if action not in self.tools:
                observation = (
                    f"错误：工具 {action} 不存在。可用工具：{', '.join(self.tools.keys())}"
                )
                history.append(
                    ReActStep(
                        thought=args.get("thought", ""), action=action,
                        action_input={k: v for k, v in args.items() if k != "thought"},
                        observation=observation, step_no=step_no, tokens=resp.tokens_used,
                    )
                )
                continue

            # 执行工具（同步工具用线程池，避免阻塞事件循环）
            tool = self.tools[action]
            call_args = {k: v for k, v in args.items() if k != "thought"}
            try:
                import anyio

                result = await anyio.to_thread.run_sync(
                    lambda: tool.func(**call_args)
                )
                observation = (
                    json.dumps(result, ensure_ascii=False)
                    if isinstance(result, (dict, list))
                    else str(result)
                )
            except Exception as e:  # noqa: BLE001
                observation = f"工具执行失败：{type(e).__name__}: {e}"

            # 观察结果过长会挤占上下文，截断
            if len(observation) > 1200:
                observation = observation[:1200] + "…（结果已截断）"

            history.append(
                ReActStep(
                    thought=args.get("thought", ""), action=action,
                    action_input=call_args, observation=observation,
                    step_no=step_no, tokens=resp.tokens_used,
                )
            )

        return {
            "success": False,
            "answer": "",
            "steps": [s.to_dict() for s in history],
            "reason": f"达到最大步数 {self.max_steps} 仍未得出结论",
            "tokens": self.total_tokens,
        }


# ---------------------------------------------------------------- 问答场景 ReAct

QA_SYSTEM_PROMPT = """你是建设工程法规问答助手，服务对象是施工企业的安全管理人员。

你的职责是依据现行有效的工程规范回答问题，绝不编造。

必须遵守的规则：
1. 只依据工具检索到的条款原文作答，不得引入检索结果之外的规范要求
2. 检索结果为空或不相关时，明确回答「知识库中未收录相关条款，无法给出可靠依据」
3. 每个结论必须标注来源条款号（规范编号+条款号）
4. 不得使用「大概」「通常」「一般而言」这类模糊表述——规范条文是确定的
5. 回答用词要专业但易懂，安全管理人员可能不是技术专家

回答格式：
先给结论，再给依据，最后给必要的提醒或提示。"""

QA_TOOLS: list[Tool] = [
    Tool(
        name="search_regulation",
        description="检索现行有效的工程法规与规范条文。回答任何法规问题都必须先用它查证。",
        func=lambda query="", category=None, top_k=5: __import__(
            "app.services.retrieval_service", fromlist=["search"]
        ).search(query=query, top_k=top_k, category=category),
        args_schema={
            "query": "检索关键词或问题原文",
            "category": "危大类别，可选：foundation_pit/formwork/lifting/scaffold/demolition/underground/other",
        },
    ),
    Tool(
        name="get_clause_detail",
        description="按规范编号和条款号获取条款完整原文。检索结果只显示片段时用它看全文。",
        func=lambda regulation_code="", clause_no="": __import__(
            "app.services.retrieval_service", fromlist=["get_clause_detail"]
        ).get_clause_detail(regulation_code, clause_no),
        args_schema={
            "regulation_code": "规范编号，如 JGJ 80-2016",
            "clause_no": "条款号，如 第4.2.1条",
        },
    ),
]


def build_qa_agent() -> ReActAgent:
    """构造法规问答 ReAct Agent。"""
    return ReActAgent(
        tools=QA_TOOLS,
        system_prompt=QA_SYSTEM_PROMPT,
        max_steps=5,
        temperature=0.1,
    )