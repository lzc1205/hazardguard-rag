"""业务枚举与常量。

集中定义而非散落在各处——审查状态、危大类别这些值会同时出现在
数据库约束、Pydantic 校验、Agent 判断和前端展示里，多处硬编码极易写串。
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """用户角色。权限由 api/deps.require_roles 控制。"""

    ADMIN = "admin"
    SUPERVISOR = "supervisor"
    REVIEWER = "reviewer"
    TECHNICIAN = "technician"
    VIEWER = "viewer"


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    REVIEWED = "reviewed"
    ARCHIVED = "archived"


class DocumentCategory(StrEnum):
    SPECIAL_PLAN = "special_plan"
    REGULATION = "regulation"
    CASE = "case"
    TEMPLATE = "template"
    OTHER = "other"


class ParseStatus(StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    PARSED = "parsed"
    FAILED = "failed"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewType(StrEnum):
    FULL = "full"
    QUICK = "quick"
    EXPERT_ONLY = "expert_only"


class ReviewConclusion(StrEnum):
    PASS = "pass"
    PASS_WITH_MODIFICATION = "pass_with_modification"
    FAIL = "fail"


class ReviewDimension(StrEnum):
    """8 个审查维度（需求文档 附录 A.2）。"""

    COMPLETENESS = "completeness"
    PROCEDURE = "procedure"
    TECHNICAL_PARAMS = "technical_params"
    CALCULATION = "calculation"
    SAFETY_MEASURE = "safety_measure"
    MONITORING = "monitoring"
    ACCEPTANCE = "acceptance"
    EMERGENCY = "emergency"


class ComplianceStatus(StrEnum):
    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NOT_APPLICABLE = "not_applicable"


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    SUGGESTION = "suggestion"


class HazardCategory(StrEnum):
    """危大工程七大类（住建部令第37号）。"""

    FOUNDATION_PIT = "foundation_pit"
    FORMWORK = "formwork"
    LIFTING = "lifting"
    SCAFFOLD = "scaffold"
    DEMOLITION = "demolition"
    UNDERGROUND = "underground"
    OTHER = "other"


class KnowledgeEntryType(StrEnum):
    REGULATION = "regulation"
    STANDARD = "standard"
    CASE = "case"
    TEMPLATE = "template"
    FAQ = "FAQ"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"


class KnowledgeBuildType(StrEnum):
    FULL_BUILD = "full_build"
    INCREMENTAL_UPDATE = "incremental_update"
    REBUILD = "rebuild"
    DELETE = "delete"


class RiskLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------- 附录 A 常量

HAZARD_CATEGORIES: dict[str, dict[str, str]] = {
    HazardCategory.FOUNDATION_PIT: {"name": "基坑工程", "icon": "土"},
    HazardCategory.FORMWORK: {"name": "模板工程及支撑体系", "icon": "模"},
    HazardCategory.LIFTING: {"name": "起重吊装及安装拆卸工程", "icon": "吊"},
    HazardCategory.SCAFFOLD: {"name": "脚手架工程", "icon": "架"},
    HazardCategory.DEMOLITION: {"name": "拆除爆破工程", "icon": "拆"},
    HazardCategory.UNDERGROUND: {"name": "暗挖工程", "icon": "挖"},
    HazardCategory.OTHER: {"name": "其他", "icon": "其"},
}

REVIEW_DIMENSIONS: dict[str, str] = {
    ReviewDimension.COMPLETENESS: "方案完整性",
    ReviewDimension.PROCEDURE: "审批程序",
    ReviewDimension.TECHNICAL_PARAMS: "技术参数",
    ReviewDimension.CALCULATION: "计算书",
    ReviewDimension.SAFETY_MEASURE: "安全措施",
    ReviewDimension.MONITORING: "监测方案",
    ReviewDimension.ACCEPTANCE: "验收要求",
    ReviewDimension.EMERGENCY: "应急处置",
}

COMPLIANCE_STATUS: list[str] = [s.value for s in ComplianceStatus]
SEVERITY: list[str] = [s.value for s in Severity]
REVIEW_CONCLUSION: list[str] = [c.value for c in ReviewConclusion]