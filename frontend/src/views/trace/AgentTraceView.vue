<script setup lang="ts">
/**
 * Agent 编排记录页。
 *
 * 把「系统是怎么得出这个结论的」完整摊开——
 * 这是本项目可观测性（JD 11%）与成本优化（JD 10%）的对外展示面。
 *
 * 三块内容：
 * 1. 执行链时间线 —— 每个 Agent 的思考、动作、观察
 * 2. Token 与成本 —— 按环节拆分的预算使用
 * 3. 拒答统计 —— 哪些环节因证据不足拒绝下结论
 */
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { reviewApi, vectorApi, promptApi, type TraceEntry } from "@/api";
import { AGENT_LABELS } from "@/utils/constants";

interface ReviewRow {
  id: string;
  title: string;
  is_over_scale: boolean | null;
  overall_score: number | null;
  overall_conclusion: string | null;
  created_at: string | null;
}

const reviews = ref<ReviewRow[]>([]);
const currentId = ref("");
const trace = ref<TraceEntry[]>([]);
const tokenSummary = ref<Record<string, any> | null>(null);
const costEstimate = ref(0);
const loading = ref(false);
const traceLoading = ref(false);

const vectorStats = ref<Record<string, any> | null>(null);
const llmStatus = ref<Record<string, any> | null>(null);
const promptCount = ref(0);

const STAGE_LABELS: Record<string, string> = {
  document_parse: "文档解析",
  hazard_identify: "危大识别",
  regulation_retrieval: "法规检索",
  compliance_review: "合规审查",
  expert_argumentation: "专家论证",
  report_generate: "报告生成",
};

const selected = computed(() =>
  reviews.value.find((r) => r.id === currentId.value),
);

const totals = computed(() => {
  const t = trace.value;
  return {
    steps: t.length,
    duration: t.reduce((s, x) => s + (x.duration_ms || 0), 0),
    tokens: t.reduce((s, x) => s + (x.tokens || 0), 0),
    refusals: t.reduce((s, x) => s + (x.refusals || 0), 0),
    degraded: t.filter((x) => x.degraded).length,
    errors: t.filter((x) => x.error).length,
  };
});

/** 每个环节的耗时占比，用于时间线上的进度条。 */
function percentOf(ms: number): number {
  if (!totals.value.duration) return 0;
  return Math.max(Math.round((ms / totals.value.duration) * 100), 2);
}

function agentIcon(name: string): string {
  if (name.includes("Parser")) return "Document";
  if (name.includes("Hazard")) return "Warning";
  if (name.includes("Retrieval")) return "Search";
  if (name.includes("Compliance")) return "Checked";
  if (name.includes("Expert")) return "ChatLineSquare";
  if (name.includes("Report")) return "Tickets";
  return "Operation";
}

async function loadReviews() {
  try {
    const res = await reviewApi.list({ page_size: 50 });
    reviews.value = res.data.items || [];
    if (reviews.value.length && !currentId.value) {
      await loadTrace(reviews.value[0].id);
    }
  } catch {
    ElMessage.error("加载审查列表失败");
  }
}

async function loadTrace(id: string) {
  traceLoading.value = true;
  try {
    const res = await reviewApi.trace(id);
    trace.value = res.data.trace || [];
    costEstimate.value = res.data.cost_estimate || 0;
    tokenSummary.value = (res.data as any).token_summary || null;
    currentId.value = id;
  } catch {
    ElMessage.error("加载执行轨迹失败");
  } finally {
    traceLoading.value = false;
  }
}

async function loadSideInfo() {
  try {
    const [v, l, p] = await Promise.all([
      vectorApi.stats(),
      promptApi.llmStatus(),
      promptApi.list(),
    ]);
    vectorStats.value = v.data;
    llmStatus.value = l.data;
    promptCount.value = (p.data || []).length;
  } catch {
    // 侧栏信息失败不阻塞主流程
  }
}

function fmtTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

onMounted(() => {
  loadReviews();
  loadSideInfo();
});
</script>

<template>
  <div class="trace-page">
    <!-- 左：审查列表 -->
    <aside class="pane pane-list">
      <div class="pane-head">
        <span class="pane-title">审查记录</span>
        <el-badge :value="reviews.length" type="info" />
      </div>
      <div
        v-for="r in reviews"
        :key="r.id"
        class="review-item"
        :class="{ active: currentId === r.id }"
        @click="loadTrace(r.id)"
      >
        <div class="ri-title">{{ r.title }}</div>
        <div class="ri-meta">
          <el-tag size="small" effect="plain" :type="(r.overall_score ?? 0) >= 80 ? 'success' : 'warning'">
            {{ r.overall_score ?? "-" }} 分
          </el-tag>
          <el-tag v-if="r.is_over_scale" size="small" type="warning" effect="plain">超规模</el-tag>
          <span class="ri-time">{{ fmtTime(r.created_at) }}</span>
        </div>
      </div>
      <el-empty v-if="!reviews.length" description="暂无审查记录" :image-size="60" />
    </aside>

    <!-- 中：执行链时间线 -->
    <section class="pane pane-trace">
      <div class="trace-header">
        <div>
          <h3>Agent 执行链</h3>
          <span v-if="selected" class="trace-sub">{{ selected.title }}</span>
        </div>
        <div v-if="trace.length" class="trace-totals">
          <span>{{ totals.steps }} 环节</span>
          <span>{{ totals.duration }}ms</span>
          <span>{{ totals.tokens }} tokens</span>
          <el-tag v-if="totals.refusals" size="small" type="warning" effect="plain">
            拒答 {{ totals.refusals }} 次
          </el-tag>
          <el-tag v-if="totals.degraded" size="small" type="warning" effect="plain">
            降级 {{ totals.degraded }} 次
          </el-tag>
          <el-tag v-if="totals.errors" size="small" type="danger" effect="plain">
            异常 {{ totals.errors }} 次
          </el-tag>
        </div>
      </div>

      <div v-loading="traceLoading" class="timeline-wrap">
        <el-timeline v-if="trace.length">
          <el-timeline-item
            v-for="(t, i) in trace"
            :key="i"
            :type="t.error ? 'danger' : t.refusals > 0 ? 'warning' : t.degraded ? 'info' : 'primary'"
            :hollow="t.refusals > 0"
            :timestamp="`第 ${i + 1} 步 · ${t.duration_ms}ms`"
            placement="top"
          >
            <div class="node">
              <div class="node-head">
                <el-icon class="node-icon"><component :is="agentIcon(t.agent)" /></el-icon>
                <span class="node-name">{{ AGENT_LABELS[t.agent] || t.agent }}</span>
                <el-tag size="small" effect="plain">{{ t.agent }}</el-tag>
                <el-tag v-if="t.degraded" size="small" type="warning" effect="plain">降级执行</el-tag>
                <span class="grow" />
                <span class="node-tokens">{{ t.tokens }} tokens</span>
              </div>

              <el-progress
                :percentage="percentOf(t.duration_ms)"
                :stroke-width="5"
                :show-text="false"
                class="node-bar"
              />

              <div v-if="t.input_summary" class="node-block">
                <div class="nb-label">输入</div>
                <div class="nb-text">{{ t.input_summary }}</div>
              </div>

              <div v-if="t.output_summary" class="node-block">
                <div class="nb-label">输出</div>
                <div class="nb-text">{{ t.output_summary }}</div>
              </div>

              <div v-if="t.tools_called?.length" class="node-block">
                <div class="nb-label">工具调用</div>
                <el-tag
                  v-for="(tc, j) in t.tools_called"
                  :key="j"
                  size="small"
                  type="warning"
                  effect="plain"
                  class="tool-tag"
                >
                  {{ tc }}
                </el-tag>
              </div>

              <div v-if="t.refusals > 0" class="node-block refusal-block">
                <div class="nb-label">拒答记录</div>
                <span>本环节有 {{ t.refusals }} 项因证据不足拒绝出具判定</span>
              </div>

              <div v-if="t.error" class="node-error">{{ t.error }}</div>
            </div>
          </el-timeline-item>
        </el-timeline>

        <el-empty v-else-if="!traceLoading" description="选择左侧记录查看执行过程" />
      </div>
    </section>

    <!-- 右：成本与系统状态 -->
    <aside class="pane pane-side">
      <!-- Token 预算 -->
      <el-card shadow="never" class="side-card">
        <template #header><span class="pane-title">Token 预算使用</span></template>
        <div v-if="tokenSummary?.by_stage?.length">
          <div v-for="s in tokenSummary.by_stage" :key="s.stage" class="budget-row">
            <span class="br-label">{{ STAGE_LABELS[s.stage] || s.stage }}</span>
            <el-progress
              :percentage="Math.min(s.usage_pct, 100)"
              :stroke-width="6"
              :show-text="false"
              :color="s.usage_pct > 80 ? '#f56c6c' : '#409eff'"
            />
            <span class="br-value">{{ s.used }}</span>
          </div>
          <div class="cost-total">
            <span>本次成本</span>
            <strong>¥{{ costEstimate.toFixed(5) }}</strong>
          </div>
        </div>
        <el-empty v-else description="暂无预算数据" :image-size="50" />
      </el-card>

      <!-- LLM 状态 -->
      <el-card shadow="never" class="side-card">
        <template #header><span class="pane-title">运行模式</span></template>
        <div v-if="llmStatus" class="status-block">
          <el-tag :type="llmStatus.available ? 'success' : 'info'" size="small">
            {{ llmStatus.available ? "LLM 已启用" : "规则模式" }}
          </el-tag>
          <div class="status-row">
            <span>模型</span><code>{{ llmStatus.model }}</code>
          </div>
          <div class="status-note">{{ llmStatus.note }}</div>
        </div>
      </el-card>

      <!-- 检索与向量库 -->
      <el-card shadow="never" class="side-card">
        <template #header><span class="pane-title">检索与向量库</span></template>
        <div v-if="vectorStats" class="status-block">
          <div class="kv">
            <span>向量覆盖</span>
            <strong>{{ vectorStats.coverage }}%</strong>
          </div>
          <div class="kv">
            <span>已索引分块</span>
            <span>{{ vectorStats.indexed_chunks }} / {{ vectorStats.total_chunks }}</span>
          </div>
          <div class="kv">
            <span>向量维度</span><span>{{ vectorStats.dimension }}</span>
          </div>
          <div class="kv">
            <span>Prompt 模板</span><span>{{ promptCount }} 个</span>
          </div>
        </div>
      </el-card>
    </aside>
  </div>
</template>

<style scoped lang="scss">
.trace-page {
  display: grid;
  grid-template-columns: 250px 1fr 280px;
  gap: 12px;
  height: 100%;
  padding: 12px;
  background: var(--el-bg-color-page);
}

.pane {
  overflow-y: auto;
  min-height: 0;
}

.pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  background: var(--el-bg-color);
  border-radius: 4px 4px 0 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.pane-title {
  font-size: 14px;
  font-weight: 500;
}

.pane-list {
  background: var(--el-bg-color);
  border-radius: 4px;
  padding-bottom: 8px;
}

.review-item {
  padding: 10px 12px;
  cursor: pointer;
  border-left: 3px solid transparent;
  transition: background 0.15s;

  &:hover {
    background: var(--el-fill-color-light);
  }

  &.active {
    background: var(--el-color-primary-light-9);
    border-left-color: var(--el-color-primary);
  }

  .ri-title {
    font-size: 13px;
    color: var(--el-text-color-primary);
    margin-bottom: 4px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .ri-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }

  .ri-time {
    font-size: 11px;
    color: var(--el-text-color-secondary);
  }
}

.pane-trace {
  background: var(--el-bg-color);
  border-radius: 4px;
  padding: 14px;
  display: flex;
  flex-direction: column;
}

.trace-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding-bottom: 12px;
  margin-bottom: 8px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  gap: 12px;

  h3 {
    margin: 0;
    font-size: 15px;
    font-weight: 500;
  }

  .trace-sub {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  .trace-totals {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }
}

.timeline-wrap {
  flex: 1;
  overflow-y: auto;
}

.node {
  .node-head {
    display: flex;
    align-items: center;
    gap: 7px;
    flex-wrap: wrap;
  }

  .node-icon {
    color: var(--el-color-primary);
  }

  .node-name {
    font-size: 13.5px;
    font-weight: 500;
  }

  .grow {
    flex: 1;
  }

  .node-tokens {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    font-variant-numeric: tabular-nums;
  }

  .node-bar {
    margin: 7px 0;
  }

  .node-block {
    margin-top: 8px;

    .nb-label {
      font-size: 11.5px;
      color: var(--el-text-color-secondary);
      margin-bottom: 3px;
    }

    .nb-text {
      font-size: 12.5px;
      line-height: 1.6;
      color: var(--el-text-color-regular);
      background: var(--el-fill-color-light);
      padding: 7px 9px;
      border-radius: 4px;
      word-break: break-word;
    }
  }

  .tool-tag {
    margin-right: 4px;
  }

  .refusal-block {
    font-size: 12px;
    color: var(--el-color-warning);
    padding: 6px 9px;
    background: var(--el-color-warning-light-9);
    border-radius: 4px;
  }

  .node-error {
    margin-top: 7px;
    font-size: 12px;
    color: var(--el-color-danger);
  }
}

.pane-side {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.side-card :deep(.el-card__body) {
  padding: 12px;
}

.budget-row {
  display: grid;
  grid-template-columns: 62px 1fr 46px;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 11.5px;

  .br-label {
    color: var(--el-text-color-secondary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .br-value {
    text-align: right;
    color: var(--el-text-color-secondary);
    font-variant-numeric: tabular-nums;
  }
}

.cost-total {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
  font-size: 12px;
  color: var(--el-text-color-secondary);

  strong {
    font-size: 15px;
    color: var(--el-color-primary);
  }
}

.status-block {
  font-size: 12px;
}

.status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 7px;

  code {
    font-size: 11.5px;
    color: var(--el-text-color-regular);
    word-break: break-all;
  }
}

.status-note {
  margin-top: 6px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}

.kv {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 0;
  color: var(--el-text-color-secondary);

  strong {
    color: var(--el-color-primary);
  }
}

@media (max-width: 1400px) {
  .trace-page {
    grid-template-columns: 210px 1fr 250px;
  }
}
</style>