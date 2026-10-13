<script setup lang="ts">
/**
 * Agent 执行链可视化。
 *
 * 展示多智能体的每个环节：名称、耗时、token 消耗、拒答次数。
 * 这是 JD「可观测性 11%」的直接体现——面试时能讲清"为什么这次答错了"。
 */
import { computed } from "vue";
import { AGENT_LABELS, type TraceEntry } from "@/utils/constants";

const props = defineProps<{
  trace: TraceEntry[] | null;
  active?: boolean;
}>();

const entries = computed(() => props.trace || []);

const totalDuration = computed(() =>
  entries.value.reduce((sum, t) => sum + (t.duration_ms || 0), 0),
);

const totalTokens = computed(() =>
  entries.value.reduce((sum, t) => sum + (t.tokens || 0), 0),
);

const totalRefusals = computed(() =>
  entries.value.reduce((sum, t) => sum + (t.refusals || 0), 0),
);

/** 每个环节的耗时占比，用于进度条宽度。 */
function percentOf(ms: number): number {
  if (!totalDuration.value) return 0;
  return Math.max((ms / totalDuration.value) * 100, 2);
}

function agentLabel(name: string): string {
  return AGENT_LABELS[name] || name;
}

/** Agent 名 → Element Plus 图标名 */
function agentIcon(name: string): string {
  if (name.includes("Parser")) return "Document";
  if (name.includes("Hazard")) return "Warning";
  if (name.includes("Retrieval")) return "Search";
  if (name.includes("Compliance")) return "Checked";
  if (name.includes("Expert")) return "ChatLineSquare";
  if (name.includes("Report")) return "Tickets";
  return "Operation";
}
</script>

<template>
  <div class="agent-trace">
    <div class="trace-header">
      <span class="title">
        <el-icon><Operation /></el-icon>
        Agent 执行链
      </span>
      <span v-if="active" class="running">
        <el-icon class="is-loading"><Loading /></el-icon>
        执行中
      </span>
      <span v-else-if="entries.length" class="summary">
        {{ entries.length }} 环节 · {{ totalDuration }}ms · {{ totalTokens }} tokens
        <el-tag v-if="totalRefusals > 0" size="small" type="warning" effect="plain">
          拒答 {{ totalRefusals }} 次
        </el-tag>
      </span>
    </div>

    <el-timeline v-if="entries.length" class="trace-list">
      <el-timeline-item
        v-for="(t, idx) in entries"
        :key="idx"
        :type="t.error ? 'danger' : t.refusals > 0 ? 'warning' : 'primary'"
        :hollow="t.refusals > 0"
        size="normal"
      >
        <div class="trace-item">
          <div class="item-head">
            <el-icon class="agent-icon"><component :is="agentIcon(t.agent)" /></el-icon>
            <span class="agent-name">{{ agentLabel(t.agent) }}</span>
            <el-tag v-if="t.degraded" size="small" type="warning" effect="plain">
              降级
            </el-tag>
            <span class="item-metrics">
              <span>{{ t.duration_ms }}ms</span>
              <span v-if="t.tokens">{{ t.tokens }} tok</span>
              <span v-if="t.refusals > 0" class="refusal">
                拒答 {{ t.refusals }}
              </span>
            </span>
          </div>

          <el-progress
            :percentage="Math.round(percentOf(t.duration_ms))"
            :stroke-width="4"
            :show-text="false"
            class="duration-bar"
          />

          <div v-if="t.output_summary" class="item-output">
            {{ t.output_summary }}
          </div>

          <div v-if="t.error" class="item-error">{{ t.error }}</div>
        </div>
      </el-timeline-item>
    </el-timeline>

    <el-empty v-else-if="!active" description="暂无执行记录" :image-size="60" />
    <el-skeleton v-else :rows="3" animated />
  </div>
</template>

<style scoped lang="scss">
.agent-trace {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.trace-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 12px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);

  .title {
    display: flex;
    align-items: center;
    gap: 6px;
    font-weight: 500;
    font-size: 14px;
    color: var(--el-text-color-primary);
  }

  .running {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    color: var(--el-color-primary);
  }

  .summary {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    display: flex;
    align-items: center;
    gap: 8px;
  }
}

.trace-list {
  flex: 1;
  overflow-y: auto;
  padding-left: 4px;
}

.trace-item {
  .item-head {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }

  .agent-icon {
    color: var(--el-color-primary);
  }

  .agent-name {
    font-size: 13px;
    font-weight: 500;
    color: var(--el-text-color-primary);
  }

  .item-metrics {
    margin-left: auto;
    display: flex;
    gap: 10px;
    font-size: 12px;
    color: var(--el-text-color-secondary);
    font-variant-numeric: tabular-nums;

    .refusal {
      color: var(--el-color-warning);
      font-weight: 500;
    }
  }

  .duration-bar {
    margin: 6px 0 4px;
  }

  .item-output {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    line-height: 1.5;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .item-error {
    margin-top: 4px;
    font-size: 12px;
    color: var(--el-color-danger);
  }
}
</style>