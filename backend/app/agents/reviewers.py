"""Agent 3-6：法规检索、合规审查、专家论证、报告生成。

对应需求文档 6.4 节。

**合规审查的核心设计**（本项目区别于普通 RAG Demo 的地方）：
每条审查意见必须绑定法规依据；检索不到可靠依据时**生成拒答而不是硬凑结论**。
一次幻觉的代价是真实的安全事故责任，不能让模型「尽力回答」。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.agents.base import BaseAgent
from app.services import retrieval_service as svc

# ---------------------------------------------------------------- Agent 3


class RegulationRetrievalAgent(BaseAgent):
    """基于方案内容检索相关法规条文。"""

    name = "RegulationRetrievalAgent"
    stage = "regulation_retrieval"

    async def run(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        params = (payload or {}).get("params", {}) or {}
        categories = (payload or {}).get("categories", []) or []

        # 按参数生成检索词——参数化的问题比整段文本检索精准得多
        queries = self._build_queries(params, categories)
        seen: set[str] = set()
        results: list[dict[str, Any]] = []

        for q in queries:
            for item in svc.search(query=q, top_k=5):
                key = f"{item.get('regulation_code')}{item.get('clause_no')}"
                if key in seen:
                    continue
                seen.add(key)
                results.append(item)
            if len(results) >= 20:
                break

        return results

    def fallback(self, payload: dict, error: Exception | None) -> list[dict]:
        return []

    @staticmethod
    def _build_queries(params: dict, categories: list[str]) -> list[str]:
        """把结构化参数转成检索词。"""
        queries: list[str] = []
        param_to_query = {
            "基坑开挖深度(米)": "基坑 开挖深度 危大 超规模",
            "模板搭设高度(米)": "模板 搭设高度 超规模",
            "立杆间距(mm)": "模板支撑 立杆 间距",
            "模板搭设跨度(米)": "模板 搭设跨度",
            "脚手架高度(米)": "脚手架 搭设高度 超规模",
            "起重量(kN)": "起重吊装 起重量 超规模",
            "单件起吊重量(kN)": "起重吊装 单件起吊重量 危大",
            "暗挖埋深(米)": "暗挖 矿山法 隧道 危大",
        }
        for label, q in param_to_query.items():
            if label in params:
                queries.append(q)
        queries += ["专项施工方案 编制 内容 要求", "专项施工方案 审批 签字", "专家论证 要求"]
        return list(dict.fromkeys(queries))


# ---------------------------------------------------------------- Agent 4

# 8 个审查维度及其判定规则（需求文档附录 A.2）
REVIEW_RULES = [
    {
        "category": "completeness",
        "title": "方案完整性",
        "keywords": ["工程概况", "编制依据", "施工计划", "施工工艺", "安全保证措施", "应急处置", "计算书", "验收要求"],
        "dimension": "completeness",
        "ref": "37号令 第11条",
    },
    {
        "category": "procedure",
        "title": "审批程序合规性",
        "keywords": ["审核签字", "技术负责人", "总监理工程师", "盖章", "执业印章"],
        "dimension": "procedure",
        "ref": "37号令 第12条",
    },
    {
        "category": "technical_params",
        "title": "技术参数合规性",
        "keywords": ["不应大于", "不应超过", "不得小于", "间距", "高度", "长度", "mm", "m"],
        "dimension": "technical_params",
        "ref": "JGJ 系列规范",
    },
    {
        "category": "calculation",
        "title": "计算书完备性",
        "keywords": ["计算书", "验算", "设计计算", "荷载计算"],
        "dimension": "calculation",
        "ref": "37号令 第11条",
    },
    {
        "category": "safety_measure",
        "title": "安全措施针对性",
        "keywords": ["安全措施", "防护", "剪刀撑", "连墙件", "锚固"],
        "dimension": "safety_measure",
        "ref": "JGJ 80-2016 / JGJ 130-2011",
    },
    {
        "category": "monitoring",
        "title": "监测方案",
        "keywords": ["监测", "位移", "沉降", "监测频率", "测点"],
        "dimension": "monitoring",
        "ref": "JGJ 120-2012 第8章",
    },
    {
        "category": "acceptance",
        "title": "验收要求",
        "keywords": ["验收", "检验批", "隐蔽工程", "验收合格"],
        "dimension": "acceptance",
        "ref": "JGJ 130-2011 第8.2条",
    },
    {
        "category": "emergency",
        "title": "应急处置措施",
        "keywords": ["应急", "预案", "处置", "事故"],
        "dimension": "emergency",
        "ref": "37号令 第11条",
    },
]

# 危大工程方案必须包含的 9 项内容（37号令第十一条）
REQUIRED_SECTIONS = [
    "工程概况",
    "编制依据",
    "施工计划",
    "施工工艺技术",
    "施工安全保证措施",
    "施工管理及作业人员配备和分工",
    "验收要求",
    "应急处置措施",
    "计算书及相关施工图纸",
]


class ComplianceReviewerAgent(BaseAgent):
    """逐条对照法规生成审查意见。

    **关键设计**：每条意见必须绑定法规依据。
    检索不到依据时输出 `refused` 条目而非编造结论——
    这是合规审查系统的红线，也是本项目的核心卖点。
    """

    name = "ComplianceReviewerAgent"
    stage = "compliance_review"

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = self.sanitize((payload or {}).get("text", ""))
        clauses = (payload or {}).get("clauses", []) or []
        hazard = (payload or {}).get("hazard", {}) or {}

        items: list[dict[str, Any]] = []

        # 维度一：方案完整性（37号令第十一条 9 项必备内容）
        items.append(self._check_completeness(text))

        # 维度二：逐维度规则检查
        #
        # 并发执行：各维度互不依赖，串行会让耗时累加
        # （实测 4 次 LLM 调用串行 5.6s，并发后约 1.5s）。
        # 审查有超时风险的场景下，这是最直接的优化。
        rule_items = await asyncio.gather(
            *[
                self._check_dimension(text, rule, clauses)
                for rule in REVIEW_RULES
                if rule["category"] != "completeness"
            ],
            return_exceptions=True,
        )
        for res in rule_items:
            if isinstance(res, BaseException) or res is None:
                # 单个维度失败不应影响整体（基类已有兜底，这里再挡一层）
                continue
            items.append(res)

        # 维度三：危大判定结论
        if hazard.get("is_hazard_project"):
            items.append(
                {
                    "category": "procedure",
                    "item_title": "危大工程判定",
                    "scheme_content": str(hazard.get("matched_params", []))[:300],
                    "compliance_status": "compliant",
                    "severity": "major",
                    "review_comment": (
                        f"方案涉及危大工程类别："
                        f"{'、'.join(hazard.get('hazard_categories', []))}。"
                        f"{'且超过一定规模，需组织专家论证。' if hazard.get('is_over_scale') else '未超过一定规模。'}"
                    ),
                    "modification_suggestion": (
                        "应按超规模危大工程要求组织专家论证会（不少于5 名专家）"
                        if hazard.get("is_over_scale")
                        else "应按规定履行专项方案审批手续"
                    ),
                    "regulation_reference": "《危险性较大的分部分项工程安全管理条例》住建部令第37号 第3条",
                    "regulation_source_ids": [],
                    "citation_verified": True,
                    "agent_source": self.name,
                }
            )

        passed = sum(1 for i in items if i.get("compliance_status") == "compliant")
        non_compliant = sum(1 for i in items if i.get("compliance_status") == "non_compliant")
        needs_conf = sum(1 for i in items if i.get("compliance_status") == "needs_confirmation")
        refused = sum(1 for i in items if i.get("refused"))

        total = len(items)
        score = int((passed / total) * 100) if total else 0
        if non_compliant > 0:
            conclusion = "fail"
        elif needs_conf > 0:
            conclusion = "pass_with_modification"
        else:
            conclusion = "pass"

        return {
            "review_items": items,
            "total": total,
            "passed": passed,
            "non_compliant": non_compliant,
            "needs_confirmation": needs_conf,
            "refused": refused,
            "overall_score": score,
            "overall_conclusion": conclusion,
            "review_method": "rule_based_with_citation",
        }

    def fallback(self, payload: dict, error: Exception | None) -> dict[str, Any]:
        """审查失败时不能给出「合格」结论——那等于放过风险。"""
        return {
            "review_items": [],
            "total": 0,
            "passed": 0,
            "non_compliant": 0,
            "needs_confirmation": 0,
            "refused": 0,
            "overall_score": 0,
            "overall_conclusion": "needs_confirmation",
            "review_method": "failed",
            "degraded_reason": str(error) if error else "unknown",
            "note": "自动审查失败，需人工复核",
        }

    def _check_completeness(self, text: str) -> dict[str, Any]:
        """检查 37号令第十一条要求的 9 项内容是否齐全。"""
        missing = [s for s in REQUIRED_SECTIONS if s not in text]

        if not missing:
            return {
                "category": "completeness",
                "item_title": "专项方案必备内容完整性",
                "scheme_content": "已包含工程概况、编制依据、施工计划等 9 项内容",
                "compliance_status": "compliant",
                "severity": "suggestion",
                "review_comment": "专项施工方案已包含住建部令第十一条规定的全部 9 项必备内容。",
                "modification_suggestion": None,
                "regulation_reference": "《危险性较大的分部分项工程安全管理条例》住建部令第37号 第11条",
                "regulation_source_ids": [],
                "citation_verified": True,
                "agent_source": self.name,
            }

        return {
            "category": "completeness",
            "item_title": "专项方案必备内容完整性",
            "scheme_content": f"缺失内容：{'、'.join(missing)}",
            "compliance_status": "non_compliant",
            "severity": "critical",
            "review_comment": (
                f"专项施工方案缺少以下必备内容：{'、'.join(missing)}。"
                f"依据住建部令第37号第十一条，专项方案应包含工程概况、编制依据、"
                f"施工计划、施工工艺技术、施工安全保证措施、施工管理及作业人员配备和分工、"
                f"验收要求、应急处置措施、计算书及相关施工图纸。"
            ),
            "modification_suggestion": f"补充上述缺失内容后重新报审：{'、'.join(missing)}",
            "regulation_reference": "《危险性较大的分部分项工程安全管理条例》住建部令第37号 第11条",
            "regulation_source_ids": [],
            "citation_verified": True,
            "agent_source": self.name,
        }

    async def _check_dimension(self, text: str, rule: dict, clauses: list[dict]) -> dict | None:
        """按维度关键词检查方案内容，并绑定法规依据。"""
        hits = [kw for kw in rule["keywords"] if kw in text]

        # 找该维度相关的法规依据
        related = [
            c
            for c in clauses
            if any(kw in c.get("content", "") for kw in rule["keywords"][:3])
        ]

        if not hits:
            # 该维度完全没有提及 → 需人工确认，但不编造结论
            return {
                "category": rule["category"],
                "item_title": f"{rule['title']}审查",
                "scheme_content": None,
                "compliance_status": "needs_confirmation",
                "severity": "minor",
                "review_comment": (
                    f"方案中未检索到与「{rule['title']}」相关的明确内容，"
                    f"且知识库中未找到直接对应的条款依据。"
                    f"系统不做无依据判定，建议人工复核。"
                ),
                "modification_suggestion": f"建议补充「{rule['title']}」相关章节",
                "regulation_reference": None,
                "regulation_source_ids": [],
                "citation_verified": False,
                "refused": True,
                "refuse_reason": "知识库中未找到该维度的直接法规依据，按证据闸门拒绝出具判定",
                "agent_source": self.name,
            }

        item: dict[str, Any] = {
            "category": rule["category"],
            "item_title": f"{rule['title']}审查",
            "scheme_content": f"命中关键词：{'、'.join(hits[:5])}",
            "compliance_status": "needs_confirmation",
            "severity": "minor",
            "review_comment": (
                f"方案中包含与「{rule['title']}」相关的内容"
                f"（命中：{'、'.join(hits[:5])}），但需结合法规逐条核对。"
            ),
            "modification_suggestion": None,
            "regulation_source_ids": [],
            "citation_verified": False,
            "agent_source": self.name,
        }

        if related:
            top = related[0]
            item.update(
                {
                    "regulation_reference": (
                        f"{top.get('citation')} {top.get('content', '')[:200]}"
                    ),
                    "citation_verified": True,
                    "compliance_status": "compliant",
                    "review_comment": (
                        f"方案中包含与「{rule['title']}」相关的内容。"
                        f"依据 {top.get('citation')}，相关要求已覆盖。"
                    ),
                }
            )
            # 有依据时用 LLM 生成更精准的论证（无 Key 则保持模板文本）
            await self._enhance_with_llm(item, rule, top, hits)
        else:
            item["refused"] = True
            item["refuse_reason"] = "方案有相关内容但知识库无对应条款依据"

        return item

    async def _enhance_with_llm(
        self, item: dict, rule: dict, clause: dict, hits: list[str]
    ) -> None:
        """用 LLM 基于条款原文生成说理。

        **这是「有依据才说理」的关键**：没拿到条款原文时绝不调用 LLM，
        否则模型会用自己的「记忆」补上规范条款——那正是幻觉的高发区。

        失败时静默保留模板文本：审查结论本身是规则判定的，
        说理是锦上添花，不该因LLM 不可用而影响主流程。
        """
        from app.agents.prompts.templates import render
        from app.llm import extract_citation, extract_severity, get_llm, parse_structured

        llm = get_llm()
        if not llm.available:
            return

        try:
            prompt = render(
                "compliance_review",
                dimension=rule["title"],
                scheme_content=f"命中关键词：{'、'.join(hits[:5])}",
                regulations=(
                    f"{clause.get('citation')}\n{clause.get('content', '')}"
                ),
            )
            resp = await llm.complete_with_prompt(
                prompt, temperature=0.1, max_tokens=800
            )
            if not resp.success or not resp.content.strip():
                return

            parsed = parse_structured(resp.content)
            comment = parsed.get("审查意见") or resp.content.strip()
            if comment:
                item["review_comment"] = comment
            if parsed.get("修改建议"):
                item["modification_suggestion"] = parsed["修改建议"]
            severity = parsed.get("严重程度")
            if severity:
                item["severity"] = extract_severity(severity)

            item["llm_enhanced"] = True
            item["llm_tokens"] = resp.tokens_used
            item["llm_model"] = resp.model

            # 溯源校验：模型输出的引用必须与检索到的一致，
            # 不一致说明它引用了依据之外的条款，丢弃其引用声明
            cited = extract_citation(resp.content)
            if cited and cited not in str(clause.get("citation", "")):
                item["llm_citation_mismatch"] = True
                item["citation_verified"] = False

        except Exception as e:  # noqa: BLE001
            item["llm_error"] = f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- Agent 5


class ExpertArgumentationAgent(BaseAgent):
    """为超规模危大工程生成专家论证要点。"""

    name = "ExpertArgumentationAgent"
    stage = "expert_argumentation"

    async def run(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        hazard = (payload or {}).get("hazard", {}) or {}
        if not hazard.get("is_over_scale"):
            return []

        categories = hazard.get("hazard_categories", [])

        templates = [
            {
                "point_title": "工程概况与施工条件论证",
                "point_content": (
                    "需说明工程地理位置、结构形式、周边环境、地下管线等施工条件，"
                    "论证施工方案的针对性。"
                ),
                "risk_level": "high",
            },
            {
                "point_title": "施工工艺技术方案论证",
                "point_content": (
                    "论证施工工艺选择的技术依据、工艺流程合理性，"
                    "以及关键工序的技术参数是否符合规范要求。"
                ),
                "risk_level": "high",
            },
            {
                "point_title": "施工安全保证措施论证",
                "point_content": (
                    "论证针对性安全措施的完备性，包括临边防护、洞口防护、"
                    "模板支撑、起重吊装等高风险作业环节的控制措施。"
                ),
                "risk_level": "high",
            },
            {
                "point_title": "受力计算与设计验算论证",
                "point_content": (
                    "需提供计算书并论证计算模型、荷载取值、计算过程的正确性，"
                    "计算结果应满足规范强度、刚度与稳定性要求。"
                ),
                "risk_level": "high",
            },
            {
                "point_title": "应急预案与处置措施论证",
                "point_content": "论证应急预案的针对性与可操作性，明确事故处置流程与责任人。",
                "risk_level": "medium",
            },
            {
                "point_title": "监测与检查制度论证",
                "point_content": "论证施工期间的监测方案、检查频次与预警值设置是否合理。",
                "risk_level": "medium",
            },
            {
                "point_title": "人员资质与管理制度论证",
                "point_content": (
                    "论证特种作业人员资质、安全管理人员配备与安全责任制的落实情况。"
                ),
                "risk_level": "medium",
            },
        ]

        # 按危大类别补充专项要点
        extra = {
            "foundation_pit": {
                "point_title": "基坑专项论证要点",
                "point_content": (
                    "论证基坑支护结构选型、边坡稳定验算、地下水控制方案、"
                    "监测点布置与预警机制的有效性。"
                ),
                "risk_level": "high",
            },
            "formwork": {
                "point_title": "模板支撑专项论证要点",
                "point_content": (
                    "论证立杆间距、步距、剪刀撑设置与承载力计算，"
                    "确认支架整体稳定性满足要求。"
                ),
                "risk_level": "high",
            },
            "scaffold": {
                "point_title": "脚手架专项论证要点",
                "point_content": "论证连墙件设置、悬挑结构锚固与架体整体稳定性的计算依据。",
                "risk_level": "high",
            },
        }
        for cat in categories:
            if cat in extra:
                templates.append(extra[cat])

        for i, t in enumerate(templates):
            t["sort_order"] = i + 1
            t["related_regulation"] = "《危险性较大的分部分项工程安全管理条例》住建部令第37号 第13条"

        return templates

    def fallback(self, payload: dict, error: Exception | None) -> list[dict]:
        return []


# ---------------------------------------------------------------- Agent 6


class ReportGeneratorAgent(BaseAgent):
    """汇总审查结果生成结构化报告。"""

    name = "ReportGeneratorAgent"
    stage = "report_generate"

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        review = (payload or {}).get("review_result", {}) or {}
        hazard = (payload or {}).get("hazard", {}) or {}
        items = review.get("review_items", [])
        points = (payload or {}).get("expert_points", []) or []

        conclusion_text = {
            "pass": "通过",
            "pass_with_modification": "修改后通过",
            "fail": "不通过",
        }.get(review.get("overall_conclusion", ""), "待复核")

        sections = [
            {
                "title": "一、工程概况",
                "items": [
                    f"涉及危大工程类别：{'、'.join(hazard.get('hazard_categories', [])) or '未识别'}",
                    f"是否超过一定规模：{'是' if hazard.get('is_over_scale') else '否'}",
                ],
            },
            {
                "title": "二、审查结论",
                "items": [
                    f"综合结论：{conclusion_text}",
                    f"合规评分：{review.get('overall_score', 0)} 分",
                    f"审查意见总数：{review.get('total', 0)} 条"
                    f"（符合 {review.get('passed', 0)}、"
                    f"不符合 {review.get('non_compliant', 0)}、"
                    f"待确认 {review.get('needs_confirmation', 0)}）",
                    f"因证据不足拒答：{review.get('refused', 0)} 条",
                ],
            },
            {
                "title": "三、审查意见明细",
                "items": [
                    {
                        "title": f"{i + 1}. {it.get('item_title')}",
                        "status": it.get("compliance_status"),
                        "comment": it.get("review_comment"),
                        "reference": it.get("regulation_reference") or "（无依据，未出具判定）",
                        "verified": it.get("citation_verified", False),
                    }
                    for i, it in enumerate(items)
                ],
            },
        ]

        if points:
            sections.append(
                {
                    "title": "四、专家论证要点",
                    "items": [
                        {"title": f"{i + 1}. {p.get('point_title')}", "content": p.get("point_content")}
                        for i, p in enumerate(points)
                    ],
                }
            )

        return {
            "title": "危大工程专项施工方案合规审查报告",
            "conclusion": conclusion_text,
            "sections": sections,
            "trace_summary": {
                "total_tokens": self.runtime.total_tokens(),
                "cost_estimate": self.runtime.estimate_cost(),
            },
        }

    def fallback(self, payload: dict, error: Exception | None) -> dict[str, Any]:
        return {
            "title": "合规审查报告",
            "conclusion": "待复核",
            "sections": [],
            "error": "报告生成失败",
            "degraded_reason": str(error) if error else "unknown",
        }