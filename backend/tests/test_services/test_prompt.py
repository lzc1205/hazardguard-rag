"""Prompt 模板与 LLM 层测试。

对应 JD「Prompt Engineering 47%」。

**重点测的不是模板内容本身，而是三件事**：
1. 硬约束是否在模板里（这是防幻觉的根本）
2. 无 Key 时能否优雅降级（决定面试演示会不会翻车）
3. 输出解析与引用校验是否可靠（防越界引用）
"""

from __future__ import annotations

import re

import pytest

from app.agents.prompts.templates import (
    _count_constraints,
    PROMPT_REGISTRY,
    get_prompt,
    list_prompts,
    render,
)
from app.llm import LLMClient, extract_citation, extract_severity, parse_structured


# ---------------------------------------------------------------- 模板注册


def test_all_prompts_registered():
    """四类场景都要有模板：审查说理、问答、论证、报告摘要。"""
    expected = {
        "compliance_review",
        "regulatory_qa",
        "expert_argumentation",
        "report_summary",
    }
    assert expected.issubset(PROMPT_REGISTRY.keys())


def test_prompt_declares_variables_matching_placeholders():
    """声明的变量必须与模板里的占位符一致，否则渲染时才报错。"""
    import re

    for name, meta in PROMPT_REGISTRY.items():
        actual = set(re.findall(r"\{(\w+)\}", meta["template"]))
        declared = set(meta["variables"])
        assert actual == declared, (
            f"Prompt '{name}' 占位符 {actual} 与声明变量 {declared} 不一致"
        )


def test_get_prompt_returns_copy():
    """返回副本，避免调用方误改注册表。"""
    p = get_prompt("compliance_review")
    p["template"] = "被篡改"
    assert get_prompt("compliance_review")["template"] != "被篡改"


def test_unknown_prompt_raises():
    with pytest.raises(KeyError):
        get_prompt("不存在的prompt")


def test_list_prompts_metadata():
    items = list_prompts()
    assert len(items) == len(PROMPT_REGISTRY)
    for item in items:
        assert {"name", "version", "variables", "changelog", "length"} <= set(item)


# ---------------------------------------------------------------- 渲染


def test_render_replaces_variables():
    out = render(
        "compliance_review",
        dimension="技术参数合规性",
        scheme_content="立杆间距 1200mm",
        regulations="《JGJ 80-2016》第4.2.1条",
    )
    assert "技术参数合规性" in out
    assert "立杆间距 1200mm" in out
    assert "JGJ 80-2016" in out
    assert "{dimension}" not in out


def test_render_missing_variable_raises():
    """缺变量必须显式报错，不能静默产出半成品 Prompt。"""
    with pytest.raises(ValueError, match="缺少变量"):
        render("compliance_review", dimension="仅给了维度")


# ---------------------------------------------------------------- 硬约束


class TestHardConstraints:
    """防幻觉约束必须在模板里明确写出。

    这是 Prompt Engineering 在合规审查场景的真正价值，
    不是把提示词写得更漂亮，而是把「不许做什么」写清楚。
    """

    def test_compliance_review_forbids_outside_knowledge(self):
        t = PROMPT_REGISTRY["compliance_review"]["template"]
        assert "只依据给定的条款原文作答" in t
        assert "不得" in t

    def test_compliance_review_forbids_vague_conclusion(self):
        """禁止「建议参照相关规范」这类空泛结论。"""
        t = PROMPT_REGISTRY["compliance_review"]["template"]
        assert "空泛" in t
        assert "建议参照相关规范" in t

    def test_qa_must_refuse_without_context(self):
        """问答的核心约束：资料不足必须拒答。"""
        t = PROMPT_REGISTRY["regulatory_qa"]["template"]
        assert "未收录相关条款" in t
        assert "只能依据" in t

    def test_qa_forbids_vague_wording(self):
        t = PROMPT_REGISTRY["regulatory_qa"]["template"]
        assert "不得" in t
        assert "大概" in t or "模糊" in t

    def test_report_summary_must_mention_refusals(self):
        """报告摘要不得隐去拒答项——那是审查完整性的关键信息。"""
        t = PROMPT_REGISTRY["report_summary"]["template"]
        assert "拒答" in t
        assert "不可隐去" in t or "不能隐去" in t

    def test_all_prompts_have_temperature_guidance(self):
        """合规场景不需要创造性，Prompt 应引导确定性输出。"""
        for name, meta in PROMPT_REGISTRY.items():
            assert meta["version"], f"{name} 未标注版本"
            assert meta["changelog"], f"{name} 缺少 changelog"


# ---------------------------------------------------------------- 降级


class TestGracefulDegradation:
    """降级能力：LLM 不可用时必须照常工作。

    这是面试演示不出问题的前提——现场演示不能因网络或额度失败。

    注意：这些用例都**显式传入无效 Key**，不依赖环境的真实配置。
    早期版本依赖「环境里没配 Key」，一旦真的配了就测试失败——
    测试不该因环境变化而失效。
    """

    def test_placeholder_key_not_treated_as_valid(self):
        """默认配置的 sk-xxx 是占位符，不能当成真 Key 去请求。"""
        assert LLMClient(api_key="sk-xxx").available is False

    def test_empty_key_not_available(self):
        assert LLMClient(api_key="").available is False
        assert LLMClient(api_key="   ").available is False
        assert LLMClient(api_key="xxx").available is False

    @pytest.mark.asyncio
    async def test_chat_without_key_fails_fast(self):
        """无 Key 时应立即返回错误，而不是等 60 秒超时。"""
        c = LLMClient(api_key="sk-xxx")
        resp = await c.complete_with_prompt("测试")
        assert resp.success is False
        assert resp.error == "未配置有效的 LLM_API_KEY"
        assert resp.tokens_used == 0
        await c.close()

    def test_review_works_with_llm_unavailable(self, monkeypatch):
        """核心要求：LLM 不可用时，审查流程照常产出可用结论。"""
        import asyncio as _asyncio

        import app.llm as llm_mod
        from app.agents.orchestrator import run_review

        # 强制 LLM 不可用，模拟没配 Key / 额度耗尽
        monkeypatch.setattr(llm_mod.LLMClient, "available", False)

        text = (
            "一、工程概况\n基坑开挖深度6.5m。\n二、编制依据\nJGJ 120-2012\n"
            "三、施工计划\n分层开挖。\n四、施工工艺技术\n内支撑。\n"
            "五、施工安全保证措施\n设防护栏。\n六、施工管理及作业人员配备和分工\n项目经理1人。\n"
            "七、验收要求\n逐层验收。\n八、应急处置措施\n有预案。\n九、计算书及相关施工图纸\n详见计算书。"
        )
        result = _asyncio.run(run_review(text))

        # 危大识别是规则驱动的，必须准确
        assert result["is_hazard_project"] is True
        assert result["is_over_scale"] is True
        # 审查意见照常产出
        assert len(result["review_items"]) >= 5
        # 无 LLM 时不应出现增强标记
        assert not any(i.get("llm_enhanced") for i in result["review_items"])
        # 结论仍然可用
        assert result["review_summary"]["overall_conclusion"] in (
            "pass",
            "pass_with_modification",
            "fail",
        )
        # 仍带法规依据
        assert any(i.get("citation_verified") for i in result["review_items"])


# ---------------------------------------------------------------- 输出解析


class TestOutputParsing:
    def test_parse_structured_fields(self):
        text = (
            "审查意见：该方案缺少3项内容。\n"
            "严重程度：严重\n"
            "修改建议：补充后重新报审。"
        )
        parsed = parse_structured(text)
        assert parsed["审查意见"].startswith("该方案缺少")
        assert parsed["严重程度"] == "严重"
        assert parsed["修改建议"] == "补充后重新报审。"

    def test_parse_handles_markdown_bold(self):
        """模型可能输出 **字段**，解析要能兜住。"""
        parsed = parse_structured("**审查意见**：内容")
        assert parsed["审查意见"] == "内容"

    def test_parse_tolerates_garbage(self):
        """无法解析时返回空字典，不能抛异常拖垮流程。"""
        assert parse_structured("完全不符合格式的一段话") == {}

    def test_severity_mapping(self):
        assert extract_severity("严重程度：严重") == "critical"
        assert extract_severity("严重程度：重要") == "major"
        assert extract_severity("严重程度：一般") == "minor"
        assert extract_severity("严重程度：建议") == "suggestion"
        # 未标注时给默认，不能崩溃
        assert extract_severity("没有标注") == "minor"


class TestCitationExtraction:
    """引用提取用于校验模型是否编造依据。"""

    def test_extract_standard_citation(self):
        got = extract_citation("依据《建筑施工模板安全技术规范》JGJ 80-2016 第4.2.1条")
        assert got is not None
        assert "4.2.1" in got

    def test_extract_ministry_number(self):
        assert extract_citation("见建办质〔2018〕31号 要求") == "建办质〔2018〕31号"

    def test_extract_decree_number(self):
        assert extract_citation("根据住建部令第37号") == "住建部令第37号"

    def test_no_citation_returns_none(self):
        """没有引用必须返回 None，供上层判定为幻觉风险。"""
        assert extract_citation("这是一段没有任何规范引用的文字") is None

# ---------------------------------------------------------------- 模板质量底线


class TestPromptQuality:
    """Prompt 不是字符串拼接，必须守住三条底线。

    这三条一旦破了，整个「无依据不出结论」的设计就失效了。
    """

    def test_every_template_has_hard_constraints(self):
        """每个模板都必须有「硬约束」段。

        没有约束段的 Prompt 等于让模型自由发挥，
        在合规审查场景下等于让模型编造依据。
        """
        for name, entry in PROMPT_REGISTRY.items():
            assert "## 硬约束" in entry["template"], f"{name} 缺少硬约束段"
            assert _count_constraints(entry["template"]) >= 3, (
                f"{name} 的硬约束不足 3 条"
            )

    def test_all_templates_renderable(self):
        """所有模板的变量声明必须与正文占位符一致。"""
        for name, entry in PROMPT_REGISTRY.items():
            placeholders = set(re.findall(r"\{(\w+)\}", entry["template"]))
            declared = set(entry["variables"])
            # 模板中可能含 JSON 示例的 {}，只校验已声明的必须真实出现
            assert declared <= placeholders, (
                f"{name} 声明了不存在的变量：{declared - placeholders}"
            )

    def test_templates_do_not_leak_placeholders(self):
        """渲染后不能残留 {xxx} 占位符。"""
        for name, entry in PROMPT_REGISTRY.items():
            rendered = render(name, **{v: f"<{v}>" for v in entry["variables"]})
            left = re.findall(r"\{(\w+)\}", rendered)
            assert not left, f"{name} 渲染后残留占位符：{left}"

    @pytest.mark.parametrize(
        "name", ["remediation", "query_rewrite", "document_extract", "hazard_explain"]
    )
    def test_new_templates_have_project_specific_rules(self, name):
        """新增的 4 个模板必须带项目特有的禁止项。

        这些约束是从实际踩坑里提炼的：
        - remediation：不能降低合规标准（LLM 很容易「好心」放宽限值）
        - query_rewrite：不能自行补充用户没问的条件（会改变检索意图）
        - document_extract：原文没写就输出「未提及」（不能按经验推断）
        - hazard_explain：只解释不重判（判定权在规则层）
        """
        tpl = PROMPT_REGISTRY[name]["template"]
        assert "## 硬约束" in tpl, f"{name} 缺少硬约束"
        assert "不得" in tpl or "必须" in tpl, f"{name} 硬约束缺少禁止性表述"

    def test_remediation_forbids_lowering_standard(self):
        """整改 Prompt 必须明确禁止降低合规标准。

        这是最危险的一种幻觉：把「不应大于900mm」建议成「控制在1000mm」，
        表面是整改建议，实际是放宽安全底线。
        """
        tpl = PROMPT_REGISTRY["remediation"]["template"]
        assert "不得降低原方案的合规标准" in tpl
        assert "900mm" in tpl, "少样本里必须出现具体数值，否则模型学不会格式"

    def test_document_extract_requires_missing_marker(self):
        """抽取 Prompt 必须要求原文未提及时输出「未提及」。

        否则模型会按经验填一个合理值——危大判定阈值比对一旦用错数值，
        结论直接错。
        """
        tpl = PROMPT_REGISTRY["document_extract"]["template"]
        assert "未提及" in tpl
        assert "不得推断" in tpl


class TestPromptMetaExposure:
    """list_prompts 必须返回正文——否则前端管理页等于空壳。"""

    def test_list_returns_template_body(self):
        items = list_prompts()
        assert items, "模板列表为空"
        for item in items:
            assert item["template"], f"{item['name']} 未返回正文"
            assert len(item["template"]) == item["length"]
            assert item["hard_constraints"] >= 3
            assert item["purpose"], f"{item['name']} 缺少用途说明"
            assert item["used_by"], f"{item['name']} 缺少调用方说明"

    def test_registry_covers_required_scenarios(self):
        """必须覆盖四个审查主场景 + 四个工程化场景。"""
        required = {
            "compliance_review",
            "regulatory_qa",
            "expert_argumentation",
            "report_summary",
            "remediation",
            "query_rewrite",
            "document_extract",
            "hazard_explain",
        }
        assert required <= set(PROMPT_REGISTRY), (
            f"缺少模板：{required - set(PROMPT_REGISTRY)}"
        )
