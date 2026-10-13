"""Agent 1 与 Agent 2：文档解析、危大工程识别。

对应需求文档 6.4 节。这两个 Agent 用规则实现而非 LLM——
理由：危大判定阈值是住建部令明文规定的确定值，用 LLM 判断反而引入不确定性，
且离线可跑、无token 成本。LLM 的价值在审查与论证环节。
"""

from __future__ import annotations

import re
from typing import Any

from app.agents.base import BaseAgent


# ---------------------------------------------------------------- Agent 1

# 工程概况的抽取特征
_SECTION_PATTERNS = {
    "工程概况": ["工程概况", "项目概况", "工程简介"],
    "分部分项工程": ["分部分项工程", "危大工程", "涉及分部分项"],
    "施工参数": ["施工参数", "技术参数", "主要参数", "设计参数"],
    "施工工艺": ["施工工艺", "工艺流程", "施工方法", "工艺"],
    "计算书": ["计算书", "设计计算", "验算"],
}

# 参数抽取：单位 + 数值 + 语义标签
_PARAM_PATTERNS = [
    ("foundation_pit", r"开挖深度[^\d]{0,8}(\d+(?:\.\d+)?)\s*米", "基坑开挖深度(米)"),
    ("foundation_pit", r"基坑[^\d]{0,10}(?:深度|开挖)[^\d]{0,6}(\d+(?:\.\d+)?)\s*m", "基坑开挖深度(米)"),
    ("formwork", r"搭设高度[^\d]{0,8}(\d+(?:\.\d+)?)\s*米", "模板搭设高度(米)"),
    ("formwork", r"立杆[^\d]{0,12}?(\d+(?:\.\d+)?)\s*mm", "立杆间距(mm)"),
    ("formwork", r"跨度[^\d]{0,8}(\d+(?:\.\d+)?)\s*米", "模板搭设跨度(米)"),
    ("scaffold", r"脚手架[^\d]{0,8}(?:高度|搭设)[^\d]{0,6}(\d+(?:\.\d+)?)\s*米", "脚手架高度(米)"),
    ("scaffold", r"搭设高度[^\d]{0,8}(\d+(?:\.\d+)?)\s*m", "脚手架高度(米)"),
    ("lifting", r"起重量[^\d]{0,8}(\d+(?:\.\d+)?)\s*k?N", "起重量(kN)"),
    ("lifting", r"单件起吊重量[^\d]{0,8}(\d+(?:\.\d+)?)\s*k?N", "单件起吊重量(kN)"),
    ("underground", r"(?:埋深|开挖深度)[^\d]{0,8}(\d+(?:\.\d+)?)\s*米", "暗挖埋深(米)"),
]


class DocumentParserAgent(BaseAgent):
    """从方案文档中抽取结构化工程信息。

    不用 LLM 的理由：抽取是确定性的模式匹配任务，
    LLM 会引入幻觉且每次调用都要花钱，而规则一次就能跑。
    """

    name = "DocumentParserAgent"
    stage = "document_parse"

    async def run(self, payload: str) -> dict[str, Any]:
        text = self.sanitize(payload or "")

        # 抽取各章节内容
        sections: dict[str, str] = {}
        for key, markers in _SECTION_PATTERNS.items():
            sections[key] = self._extract_section(text, markers)

        # 抽取关键参数
        params: dict[str, Any] = {}
        for category, pattern, label in _PARAM_PATTERNS:
            for m in re.finditer(pattern, text):
                value = float(m.group(1))
                params[label] = {
                    "value": value,
                    "category": category,
                    "unit": re.search(r"(米|m|mm|kN|kN/m2)", label).group(1)
                    if re.search(r"(米|m|mm|kN|kN/m2)", label)
                    else "",
                }
                break

        # 抽取标题（通常首行是方案名称）
        title = text.strip().split("\n")[0][:100] if text.strip() else ""

        return {
            "title": title,
            "sections": {k: v[:1000] for k, v in sections.items() if v},
            "params": params,
            "word_count": len(text),
            "has_calculation": bool(sections.get("计算书")),
            "extraction_method": "rule_based",
        }

    def fallback(self, payload: str, error: Exception | None) -> dict[str, Any]:
        """解析失败时退化为原文透传，不阻断流程。"""
        return {
            "title": "",
            "sections": {},
            "params": {},
            "word_count": len(payload or ""),
            "has_calculation": False,
            "extraction_method": "failed",
            "degraded_reason": str(error) if error else "unknown",
        }

    @staticmethod
    def _extract_section(text: str, markers: list[str], window: int = 800) -> str:
        for marker in markers:
            idx = text.find(marker)
            if idx < 0:
                continue
            body = text[idx + len(marker) : idx + len(marker) + window]
            return body.strip()
        return ""


# ---------------------------------------------------------------- Agent 2

# 危大工程判定阈值（需求文档 8.1 节，依据住建部令第37号 + 建办质〔2018〕31号）
HAZARD_THRESHOLDS = {
    "foundation_pit": {"param": "基坑开挖深度(米)", "hazard": 3.0, "over_scale": 5.0},
    "formwork": {"param": "模板搭设高度(米)", "hazard": 5.0, "over_scale": 8.0},
    "scaffold": {"param": "脚手架高度(米)", "hazard": 24.0, "over_scale": 50.0},
    "lifting": {"param": "起重量(kN)", "hazard": 10.0, "over_scale": 300.0},
}


class HazardIdentifierAgent(BaseAgent):
    """判定是否属于危大工程、是否超过一定规模。

    阈值是住建部令明文规定的确定值，用规则判定而非 LLM——
    审查结论必须可复现、可审计，不能让模型每次给出不同答案。
    """

    name = "HazardIdentifierAgent"
    stage = "hazard_identify"

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = (payload or {}).get("params", {}) or {}
        text = self.sanitize((payload or {}).get("text", ""))

        categories: list[str] = []
        over_scale = False
        matched: list[dict] = []

        for category, rule in HAZARD_THRESHOLDS.items():
            param = params.get(rule["param"])
            if not param:
                continue
            value = float(param["value"])
            is_hazard = value >= rule["hazard"]
            is_over = value >= rule["over_scale"]

            if is_hazard:
                categories.append(category)
                matched.append(
                    {
                        "category": category,
                        "param": rule["param"],
                        "value": value,
                        "threshold": rule["hazard"],
                        "over_scale_threshold": rule["over_scale"],
                        "is_over_scale": is_over,
                        "basis": f"{rule['param']}={value} ≥ 危大阈值 {rule['hazard']}",
                    }
                )
                over_scale = over_scale or is_over

        # 文本兜底：参数抽取失败时，用关键词判断
        if not categories:
            categories = self._detect_by_keywords(text)

        return {
            "is_hazard_project": bool(categories),
            "hazard_categories": categories,
            "is_over_scale": over_scale,
            "matched_params": matched,
            "method": "rule_based",
            "note": (
                "判定依据为住建部令第37号与建办质〔2018〕31号明文阈值"
                if categories
                else "未从方案中抽取到可判定的关键参数，建议人工复核"
            ),
        }

    def fallback(self, payload: dict, error: Exception | None) -> dict[str, Any]:
        """无法判定时必须返回「需人工确认」而不是猜一个答案。"""
        return {
            "is_hazard_project": None,
            "hazard_categories": [],
            "is_over_scale": None,
            "matched_params": [],
            "method": "failed",
            "note": "危大判定失败，需人工确认",
            "degraded_reason": str(error) if error else "unknown",
        }

    @staticmethod
    def _detect_by_keywords(text: str) -> list[str]:
        """关键词兜底识别。"""
        mapping = {
            "foundation_pit": ["基坑", "深基坑", "土方开挖", "支护"],
            "formwork": ["模板", "支撑体系", "满堂支撑"],
            "scaffold": ["脚手架", "悬挑架", "爬架"],
            "lifting": ["塔吊", "起重", "吊装", "安装拆卸"],
            "demolition": ["拆除", "爆破"],
            "underground": ["暗挖", "隧道", "矿山法", "盾构"],
        }
        found = [cat for cat, kws in mapping.items() if any(k in text for k in kws)]
        return found