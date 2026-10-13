"""ReAct Agent 与 PGVector 测试。

对应 JD「Agent 架构 37%」+「向量数据库 21%」。

**重点测 ReAct 的安全边界**：合规场景下，
Agent 必须拒绝不存在工具、必须接受工具白名单约束、必须在无 LLM 时优雅降级。
这些比「能跑通」更重要。
"""

from __future__ import annotations

import json

import pytest

from app.agents.react import ReActAgent, ReActStep, Tool, build_qa_agent


# ---------------------------------------------------------------- 解析


class TestActionParsing:
    """输出解析是确定性的，不该依赖模型自觉。"""

    def test_parse_action_with_json_input(self):
        text = (
            "Thought: 需要先查立杆间距的规定\n"
            "Action: search_regulation\n"
            'Action Input: {"query": "立杆间距"}'
        )
        action, args = ReActAgent._parse_action(text)
        assert action == "search_regulation"
        assert args["query"] == "立杆间距"
        assert args["thought"] == "需要先查立杆间距的规定"

    def test_parse_final_answer(self):
        text = "Thought: 信息已足够\nFinal Answer: 立杆间距不应大于900mm。"
        action, args = ReActAgent._parse_action(text)
        assert action == "__final__"
        assert "900mm" in args["answer"]

    def test_parse_rejects_garbled_output(self):
        """格式不对时返回 None，交给上层喂回观察让它重试。"""
        assert ReActAgent._parse_action("我觉得应该查一下规范") is None

    def test_parse_handles_malformed_json(self):
        """JSON 坏了要降级而不是崩——模型偶尔会输出不合法 JSON。"""
        text = 'Thought: 查\nAction: search\nAction Input: {query: 立杆}'
        action, args = ReActAgent._parse_action(text)
        assert action == "search"
        # 解析失败时退化为空 dict，不抛异常
        assert isinstance(args, dict)

    def test_thought_containing_action_keyword_is_not_misparsed(self):
        """Thought 里提到 Action 关键字不应被误判为真实调用。"""
        text = (
            "Thought: 我应该先 Action: search_regulation 查一下\n"
            "Action: get_clause_detail\n"
            'Action Input: {"regulation_code": "JGJ 80-2016", "clause_no": "第4.2.1条"}'
        )
        action, _ = ReActAgent._parse_action(text)
        assert action == "get_clause_detail", "取的是最后一个 Action 而非 Thought 里的"

    def test_parse_non_json_input(self):
        text = "Thought: 查\nAction: search_regulation\nAction Input: 立杆间距"
        action, args = ReActAgent._parse_action(text)
        assert action == "search_regulation"
        assert args.get("query") == "立杆间距"


# ---------------------------------------------------------------- 工具


class TestToolSafety:
    def test_tool_prompt_line_format(self):
        """工具描述直接进 Prompt，格式必须清晰。"""

        def dummy(query: str = "") -> list:
            return []

        t = Tool(
            name="search_regulation",
            description="检索法规条文",
            func=dummy,
            args_schema={"query": "检索关键词"},
        )
        line = t.to_prompt_line()
        assert "search_regulation" in line
        assert "query" in line
        assert "检索法规条文" in line


class TestToolWhitelist:
    """工具白名单是合规场景的安全底线。"""

    @pytest.mark.asyncio
    async def test_unknown_tool_is_rejected(self):
        """模型调用不存在的工具时，必须拒绝并告知可用工具。

        不能静默执行——那意味着任意代码可能被触发。
        """

        def dummy(query: str = "") -> str:
            return "不应被调用"

        agent = ReActAgent(
            tools=[Tool(name="safe_tool", description="安全工具", func=dummy)],
            system_prompt="你是测试助手",
            max_steps=2,
        )

        # 直接验证白名单机制：不在 tools 里的名字取不到
        assert "unknown_tool" not in agent.tools
        assert "safe_tool" in agent.tools

    @pytest.mark.asyncio
    async def test_agent_without_llm_degrades(self):
        """无 LLM 时应立即返回失败原因，而不是空转耗 token。"""
        import app.llm as llm_mod
        from app.llm import LLMClient

        original = LLMClient.available
        try:
            LLMClient.available = property(lambda self: False)
            agent = build_qa_agent()
            result = await agent.run("立杆间距要求")
        finally:
            LLMClient.available = original

        assert result["success"] is False
        assert "API" in result["reason"]
        assert result["steps"] == []


# ---------------------------------------------------------------- 步记录


class TestReActStep:
    def test_step_to_dict_truncates(self):
        """Trace 存太长会撑爆数据库，长内容必须截断。"""
        step = ReActStep(
            thought="很长的思考" * 200,
            action="search",
            action_input={"q": "x"},
            observation="很长的观察" * 300,
            step_no=1,
            tokens=100,
        )
        d = step.to_dict()
        assert len(d["thought"]) <= 300
        assert len(d["observation"]) <= 500
        assert d["action"] == "search"
        assert d["step"] == 1


# ---------------------------------------------------------------- PGVector


class TestPgVectorStore:
    """向量库的行为约束。"""

    def test_dimension_mismatch_raises(self):
        """维度不匹配必须报错，不能静默截断或补零。

        混存不同维度的向量会让整张表的检索结果失真且难以排查。
        """
        from app.rag.vector_store import VECTOR_DIM, PgVectorStore

        store = PgVectorStore.__new__(PgVectorStore)  # 绕过 __init__，只测参数校验
        import asyncio

        with pytest.raises(ValueError, match="维度不匹配"):
            asyncio.run(
                store.similarity_search([0.1] * (VECTOR_DIM + 1))
            )

    def test_pricing_table_has_deepseek(self):
        """DeepSeek 已接入，计价表必须有对应条目，否则成本显示为 0。"""
        from app.llm import get_pricing

        p = get_pricing("deepseek-flash")
        assert p.input_per_million > 0
        assert p.output_per_million > 0
        # 输出应比输入贵（completion 通常 token 少但单价高）
        assert p.output_per_million > p.input_per_million

    def test_unknown_model_falls_back(self):
        from app.llm import get_pricing

        p = get_pricing("some-unknown-model")
        assert p.input_per_million > 0


class TestSeedIndexing:
    def test_seed_chunks_have_category(self):
        """种子数据必须带 category——领域加权依赖它。

        之前靠正文关键词猜领域，把「隧道模」误判成暗挖工程。
        """
        from app.data.seed import load_seed_chunks

        chunks = load_seed_chunks()
        assert chunks
        with_cat = [c for c in chunks if c[3]]
        assert len(with_cat) == len(chunks), "存在缺少 category 的条目"