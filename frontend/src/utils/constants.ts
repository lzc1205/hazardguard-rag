/**
 * 全局常量与类型定义。
 *
 * 枚举值必须与后端 app/models/enums.py 严格一致——
 * 前端写死一份字符串枚举，两边不同步就会出现「界面显示未知状态」。
 */

export const API_BASE_URL = "/api/v1";

export const ROLES = {
  admin: "Admin",
  supervisor: "Supervisor",
  reviewer: "Reviewer",
  technician: "Technician",
  viewer: "Viewer",
} as const;

// ---------------------------------------------------------------- 审查状态

export const COMPLIANCE_STATUS_META: Record<
  string,
  { label: string; type: "success" | "danger" | "warning" | "info"; color: string }
> = {
  compliant: { label: "符合", type: "success", color: "#67c23a" },
  non_compliant: { label: "不符合", type: "danger", color: "#f56c6c" },
  needs_confirmation: { label: "待人工确认", type: "warning", color: "#e6a23c" },
  not_applicable: { label: "不适用", type: "info", color: "#909399" },
};

export const SEVERITY_META: Record<
  string,
  { label: string; type: "danger" | "warning" | "info" | "primary" }
> = {
  critical: { label: "严重", type: "danger" },
  major: { label: "重要", type: "warning" },
  minor: { label: "一般", type: "info" },
  suggestion: { label: "建议", type: "primary" },
};

export const CONCLUSION_META: Record<
  string,
  { label: string; type: "success" | "warning" | "danger" | "info" }
> = {
  pass: { label: "通过", type: "success" },
  pass_with_modification: { label: "修改后通过", type: "warning" },
  fail: { label: "不通过", type: "danger" },
  not_applicable: { label: "非危大工程", type: "info" },
};

// ---------------------------------------------------------------- 审查维度

export const REVIEW_DIMENSION_LABELS: Record<string, string> = {
  completeness: "方案完整性",
  procedure: "审批程序",
  technical_params: "技术参数",
  calculation: "计算书",
  safety_measure: "安全措施",
  monitoring: "监测方案",
  acceptance: "验收要求",
  emergency: "应急处置",
};

// ---------------------------------------------------------------- 危大类别

export const HAZARD_CATEGORIES = [
  { key: "foundation_pit", name: "基坑工程", icon: "土", color: "#409eff" },
  { key: "formwork", name: "模板工程及支撑体系", icon: "模", color: "#67c23a" },
  { key: "lifting", name: "起重吊装及安装拆卸工程", icon: "吊", color: "#e6a23c" },
  { key: "scaffold", name: "脚手架工程", icon: "架", color: "#f56c6c" },
  { key: "demolition", name: "拆除爆破工程", icon: "拆", color: "#909399" },
  { key: "underground", name: "暗挖工程", icon: "挖", color: "#9254de" },
  { key: "other", name: "其他", icon: "其", color: "#8c8c8c" },
] as const;

export function categoryName(key: string): string {
  return HAZARD_CATEGORIES.find((c) => c.key === key)?.name || key;
}

export function categoryMeta(key: string) {
  return HAZARD_CATEGORIES.find((c) => c.key === key) || HAZARD_CATEGORIES[6];
}

// ---------------------------------------------------------------- Agent 展示名

export const AGENT_LABELS: Record<string, string> = {
  DocumentParserAgent: "文档解析",
  HazardIdentifierAgent: "危大识别",
  RegulationRetrievalAgent: "法规检索",
  ComplianceReviewerAgent: "合规审查",
  ExpertArgumentationAgent: "专家论证",
  ReportGeneratorAgent: "报告生成",
  NonHazardConclusion: "非危大结论",
};

// ---------------------------------------------------------------- 类型

export interface ReviewItem {
  category: string;
  item_title: string;
  scheme_content: string | null;
  regulation_reference: string | null;
  compliance_status: string;
  severity: string | null;
  review_comment: string;
  modification_suggestion: string | null;
  citation_verified: boolean;
  refused?: boolean;
  refuse_reason?: string | null;
  agent_source?: string | null;
}

export interface ExpertPoint {
  point_title: string;
  point_content: string;
  risk_level: string | null;
  related_regulation: string | null;
}

export interface TraceEntry {
  agent: string;
  input_summary: string;
  output_summary: string;
  duration_ms: number;
  tokens: number;
  tools_called: string[];
  refusals: number;
  degraded: boolean;
  error: string | null;
}

export interface ReviewDetail {
  id: string;
  title: string;
  status: string;
  is_hazard_project: boolean | null;
  is_over_scale: boolean | null;
  hazard_categories: string[] | null;
  hazard_params: any;
  overall_score: number | null;
  overall_conclusion: string | null;
  review_items: ReviewItem[];
  expert_points: ExpertPoint[];
  agent_trace: TraceEntry[] | null;
  token_summary: any;
  cost_estimate: number | null;
  created_at: string | null;
}

export interface SearchHit {
  clause_no: string | null;
  regulation_name: string | null;
  regulation_code: string | null;
  category: string | null;
  page_number: number | null;
  is_mandatory: boolean;
  content: string;
  citation: string;
  score: number;
}