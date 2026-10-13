/** 审查相关 API。 */

import request from "./request";
import type { ReviewDetail, SearchHit } from "@/utils/constants";

export interface CreateReviewPayload {
  title: string;
  text: string;
  project_id?: string | null;
  document_id?: string | null;
  review_type?: "full" | "quick" | "expert_only";
  /**
   * false=同步执行（默认，1~2 秒返回完整结果）
   * true =投递 Celery 异步任务，立即返回 task_id（适合长文档）
   */
  async_mode?: boolean;
}

export interface ReviewListItem {
  id: string;
  title: string;
  status: string;
  is_hazard_project: boolean | null;
  is_over_scale: boolean | null;
  overall_score: number | null;
  overall_conclusion: string | null;
  created_at: string | null;
}

export const reviewApi = {
  /** 发起审查。耗时较长，超时设为 5 分钟。 */
  async create(payload: CreateReviewPayload) {
    return request.post("/reviews", payload, { timeout: 300000 });
  },

  async list(params?: {
    page?: number;
    page_size?: number;
    project_id?: string;
    document_id?: string;
    overall_conclusion?: string;
    /** "true" / "false" —— 只看超规模 */
    is_over_scale?: string;
    keyword?: string;
  }) {
    return request.get("/reviews", { params }) as Promise<{
      data: { items: ReviewListItem[]; total: number; page: number; page_size: number };
    }>;
  },

  async detail(id: string) {
    return request.get(`/reviews/${id}`) as Promise<{ data: ReviewDetail }>;
  },

  async trace(id: string) {
    return request.get(`/reviews/${id}/trace`) as Promise<{
      data: { trace: ReviewDetail["agent_trace"]; cost_estimate: number };
    }>;
  },

  /** 报告预览数据（与 Word/PDF 导出同一份 payload，保证三者内容一致） */
  async reportPreview(id: string) {
    return request.get(`/reviews/${id}/report.preview`) as Promise<{
      data: {
        title: string;
        report_number: string | null;
        is_hazard_project: boolean;
        is_over_scale: boolean;
        hazard_categories: string[];
        overall_score: number | null;
        overall_conclusion: string | null;
        review_items: Array<Record<string, any>>;
        expert_points: Array<Record<string, any>>;
        agent_trace: Array<Record<string, any>>;
        total_tokens: number;
        cost_estimate: number;
      };
    }>;
  },

  async remove(id: string) {
    return request.delete(`/reviews/${id}`);
  },

  /** 统计分析页数据：结论/类别分布、趋势、成本耗时 */
  async statsDashboard(days = 30) {
    return request.get("/reviews/stats/dashboard", { params: { days } }) as Promise<{
      data: {
        total: number;
        conclusion_dist: Array<{ key: string; count: number }>;
        category_dist: Array<{ key: string; count: number }>;
        daily: Array<{ date: string; count: number }>;
        cost: {
          total: number;
          total_tokens: number;
          avg_cost: number;
          avg_duration: number;
          max_duration: number;
        };
        score: { count: number; avg: number; min: number; max: number };
      };
    }>;
  },

  async stats() {
    return request.get("/reviews/stats/overview") as Promise<{
      data: {
        total_reviews: number;
        hazard_reviews: number;
        over_scale_reviews: number;
        passed_reviews: number;
        pass_rate: number;
      };
    }>;
  },
};

export const searchApi = {
  async search(params: {
    query: string;
    top_k?: number;
    category?: string;
    only_mandatory?: boolean;
  }) {
    return request.post("/search", params) as Promise<{ data: { items: SearchHit[] } }>;
  },


  async validity(code: string) {
    return request.get(`/regulations/${code}/validity`) as Promise<{
      data: { regulation_code: string; is_active: boolean; reason: string };
    }>;
  },

};

export const knowledgeApi = {
  async listEntries(params?: { page?: number; page_size?: number; category?: string }) {
    return request.get("/entries", { params });
  },

  async createEntry(payload: Record<string, unknown>) {
    return request.post("/entries", payload);
  },

  async removeEntry(id: string) {
    return request.delete(`/entries/${id}`);
  },

  /** 按规范号列出全部条款（条目详情抽屉用） */
  async clauses(code: string) {
    return request.get(`/regulations/${code}/clauses`) as Promise<{
      data: {
        items: Array<{
          clause_no: string;
          regulation_name: string;
          regulation_code: string;
          content: string;
          citation: string;
          is_mandatory: boolean;
        }>;
        total: number;
      };
    }>;
  },

  async categories() {
    return request.get("/categories") as Promise<{
      data: Array<{
        key: string;
        name: string;
        icon: string;
        hazard_threshold: string;
        over_scale_threshold: string;
      }>;
    }>;
  },

  async reload() {
    return request.post("/reload");
  },

  async runEval(payload?: { retriever?: string; tag?: string }) {
    return request.post("/eval/run", payload || {}, { timeout: 120000 });
  },
};

export interface DocumentItem {
  id: string;
  project_id: string | null;
  title: string;
  file_name: string;
  file_type: string;
  file_size: number | null;
  doc_category: string | null;
  construction_unit: string | null;
  parse_status: string;
  page_count: number | null;
  word_count: number | null;
  parsed_content_length: number;
  created_at: string | null;
  content_preview?: string;
  content_length?: number;
}

export const documentApi = {
  /**
   * 上传方案文档。
   *
   * 必须构造 FormData——后端签名是 `file: UploadFile = File(...)` +
   * `title: str = Form(...)`，传普通对象会被 FastAPI 判为缺 file 而 422。
   */
  async upload(
    file: File,
    meta: { title: string; project_id?: string; doc_category?: string; construction_unit?: string },
    onProgress?: (percent: number) => void,
  ) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", meta.title);
    if (meta.project_id) fd.append("project_id", meta.project_id);
    fd.append("doc_category", meta.doc_category || "special_plan");
    if (meta.construction_unit) fd.append("construction_unit", meta.construction_unit);

    return request.post("/documents/upload", fd, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 300000,
      onUploadProgress: (e) => {
        if (onProgress && e.total) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      },
    }) as Promise<{ data: DocumentItem }>;
  },

  async list(params?: {
    page?: number;
    page_size?: number;
    project_id?: string;
    doc_category?: string;
    parse_status?: string;
    keyword?: string;
  }) {
    return request.get("/documents", { params }) as Promise<{
      data: { items: DocumentItem[]; total: number; page: number; page_size: number };
    }>;
  },

  async detail(id: string) {
    return request.get(`/documents/${id}`) as Promise<{ data: DocumentItem }>;
  },

  /** 下载原文件。后端返回二进制，这里关掉默认的 JSON 解包 */
  async download(id: string) {
    return request.get(`/documents/${id}/download`, {
      responseType: "blob",
    }) as Promise<any>;
  },

  async remove(id: string) {
    return request.delete(`/documents/${id}`);
  },
};

export const authApi = {
  async login(username: string, password: string) {
    return request.post("/auth/login", { username, password }) as Promise<{
      data: { access_token: string; token_type: string; user: any };
    }>;
  },

  async me() {
    return request.get("/auth/me");
  },

  async logout() {
    return request.post("/auth/logout");
  },
};
// ============================================================
//  智能问答（ReAct / Function Calling）
// ============================================================

export interface ReActStep {
  step: number;
  thought: string;
  action: string;
  action_input: Record<string, unknown>;
  observation: string;
  tokens: number;
}

export interface AskResult {
  answer: string;
  sources: Array<Record<string, any>>;
  refused: boolean;
  refuse_reason: string | null;
  mode: string;
  steps: ReActStep[];
  tool_calls: Array<{ name: string; args: Record<string, unknown> }>;
  tokens: number;
  latency_ms: number;
}

export const chatApi = {
  /** mode: react（自主多轮）| function_calling（原生 tool_calls）| direct（单次检索） */
  async ask(content: string, mode: "react" | "function_calling" | "direct" = "react") {
    return request.post("/ask", { content, mode }, { timeout: 120000 }) as Promise<{
      data: AskResult;
    }>;
  },
};

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

export const traceApi = {
  /** 取某次审查的完整 Agent 执行轨迹 */
  async forReview(reviewId: string) {
    return request.get(`/reviews/${reviewId}/trace`) as Promise<{
      data: { trace: TraceEntry[]; cost_estimate: number; token_summary?: any };
    }>;
  },
};

export interface PromptInfo {
  name: string;
  version: string;
  variables: string[];
  changelog: string;
  length: number;
  /** 模板正文——管理页要能直接读到内容，否则等于没展示 */
  template: string;
  purpose: string;
  used_by: string;
  /** 硬约束条数：合规 Prompt 的核心价值指标 */
  hard_constraints: number;
  few_shot: boolean;
}

export interface PromptVersion {
  id: string;
  name: string;
  version: string;
  changelog: string;
  variables: string[];
  is_active: boolean;
  eval_score: Record<string, any> | null;
  template_length: number;
  created_at: string | null;
}

export const promptApi = {
  async list() {
    return request.get("/prompts") as Promise<{ data: PromptInfo[] }>;
  },
  async versions(name?: string) {
    return request.get("/prompts/versions", { params: { name } }) as Promise<{
      data: { items: PromptVersion[]; total: number };
    }>;
  },
  async sync(force = false) {
    return request.post("/prompts/sync", null, { params: { force } });
  },
  async llmStatus() {
    return request.get("/llm/status") as Promise<{
      data: {
        available: boolean;
        model: string;
        base_url: string;
        mode: string;
        note: string;
      };
    }>;
  },
};

export const vectorApi = {
  async stats() {
    return request.get("/vector/stats") as Promise<{
      data: {
        total_chunks: number;
        indexed_chunks: number;
        coverage: number;
        models: Record<string, number>;
        dimension: number;
      };
    }>;
  },

  /** 重建向量索引（admin）。换 Embedding 模型后必须执行，耗时较长 */
  async reindex() {
    return request.post("/vector/reindex", null, { timeout: 300000 }) as Promise<{
      data: {
        entries_created: number;
        chunks_indexed: number;
        dimension: number;
        coverage: number;
      };
    }>;
  },
};

// ============================================================
//  项目管理
// ============================================================

export interface ProjectItem {
  id: string;
  name: string;
  code: string | null;
  description: string | null;
  construction_unit: string | null;
  supervision_unit: string | null;
  location: string | null;
  start_date: string | null;
  end_date: string | null;
  status: string;
  status_label: string;
  created_at: string;
  document_count: number;
  review_count: number;
  over_scale_count: number;
}

export interface ProjectListResult {
  items: ProjectItem[];
  total: number;
  page: number;
  page_size: number;
}

export const projectApi = {
  async list(params?: { page?: number; page_size?: number; status?: string; keyword?: string }) {
    return request.get("/projects", { params }) as Promise<{ data: ProjectListResult }>;
  },
  async detail(id: string) {
    return request.get(`/projects/${id}`) as Promise<{ data: ProjectItem }>;
  },
  async create(payload: Record<string, unknown>) {
    return request.post("/projects", payload) as Promise<{ data: ProjectItem }>;
  },
  async update(id: string, payload: Record<string, unknown>) {
    return request.put(`/projects/${id}`, payload) as Promise<{ data: ProjectItem }>;
  },
  async remove(id: string) {
    return request.delete(`/projects/${id}`) as Promise<{
      data: { deleted: boolean; document_count: number; review_count: number };
    }>;
  },
  async documents(id: string) {
    return request.get(`/projects/${id}/documents`) as Promise<{ data: { items: any[]; total: number } }>;
  },
  async reviews(id: string) {
    return request.get(`/projects/${id}/reviews`) as Promise<{ data: { items: any[]; total: number } }>;
  },
};

// ============================================================
//  异步任务（Celery）
// ============================================================

export interface TaskStatus {
  task_id: string;
  status: "pending" | "processing" | "completed" | "failed";
  progress: number;
  current_stage: string | null;
  result: {
    review_id: string;
    score: number | null;
    conclusion: string | null;
    items: number;
    expert_points: number;
    is_hazard_project: boolean;
    is_over_scale: boolean;
    total_tokens: number;
    cost_estimate: number;
  } | null;
  error_message: string | null;
  created_at: string | null;
  finished_at: string | null;
}

export const taskApi = {
  /** 轮询任务进度。completed 后 result 里带 review_id，可直接跳详情页 */
  async status(taskId: string) {
    return request.get(`/reviews/tasks/${taskId}`) as Promise<{ data: TaskStatus }>;
  },
  async list(pageSize = 20) {
    return request.get("/reviews/tasks", { params: { page_size: pageSize } }) as Promise<{
      data: { items: Array<Partial<TaskStatus>>; total: number };
    }>;
  },
};

export const statsApi = {
  /** 统计分析页综合数据：结论分布 / 类别分布 / 趋势 / 成本耗时 */
  async dashboard(days = 30) {
    return request.get("/reviews/stats/dashboard", { params: { days } }) as Promise<{
      data: {
        total: number;
        conclusion_dist: Array<{ key: string; count: number }>;
        category_dist: Array<{ key: string; count: number }>;
        daily: Array<{ date: string; count: number }>;
        cost: {
          total: number;
          total_tokens: number;
          avg_cost: number;
          avg_duration: number;
          max_duration: number;
        };
        score: { count: number; avg: number; min: number; max: number };
      };
    }>;
  },
};

// ============================================================
//  用户管理（需求文档 7.4.17）
// ============================================================

export interface UserItem {
  id: string;
  username: string;
  full_name: string | null;
  email: string | null;
  phone: string | null;
  role: string;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export const ROLE_OPTIONS = [
  { value: "admin", label: "管理员", type: "danger" },
  { value: "supervisor", label: "总监", type: "warning" },
  { value: "reviewer", label: "审查员", type: "primary" },
  { value: "technician", label: "技术员", type: "success" },
  { value: "viewer", label: "访客", type: "info" },
];

export const userApi = {
  async list(params?: { page?: number; page_size?: number; keyword?: string }) {
    return request.get("/users", { params }) as Promise<{
      data: { items: UserItem[]; total: number; page?: number };
    }>;
  },
  async create(payload: {
    username: string;
    password: string;
    full_name?: string;
    email?: string;
    role?: string;
  }) {
    return request.post("/users", payload);
  },
  async update(id: string, payload: Record<string, unknown>) {
    return request.put(`/users/${id}`, payload);
  },
  async remove(id: string) {
    return request.delete(`/users/${id}`);
  },
  async setStatus(id: string, isActive: boolean) {
    return request.patch(`/users/${id}/status`, { is_active: isActive });
  },
  async setRole(id: string, role: string) {
    return request.patch(`/users/${id}/role`, { role });
  },
};
