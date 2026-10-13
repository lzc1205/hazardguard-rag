<script setup lang="ts">
/**
 * 概览页。
 *
 * 展示系统核心能力与实测指标——面试演示时的第一屏。
 * 指标直接对应 JD 高频要求，能让面试官一眼看到工程化程度。
 */
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { reviewApi } from "@/api";
import { CONCLUSION_META } from "@/utils/constants";

const router = useRouter();
const loading = ref(true);
const stats = ref({
  total_reviews: 0,
  hazard_reviews: 0,
  over_scale_reviews: 0,
  passed_reviews: 0,
  pass_rate: 0,
});
const recent = ref<
  Array<{
    id: string;
    title: string;
    overall_score: number | null;
    overall_conclusion: string | null;
    is_over_scale: boolean | null;
    created_at: string | null;
  }>
>([]);

/** 项目实测指标（来自 eval/reports/baseline.json） */
const METRICS = [
  { name: "Recall@5", value: "95.83%", desc: "条款召回率", target: "≥95%", ok: true },
  { name: "命中率 Hit@5", value: "100%", desc: "Top5 内含目标条款", target: "≥98%", ok: true },
  { name: "拒答准确率", value: "100%", desc: "无依据即拒答", target: "≥95%", ok: true },
  { name: "引用正确率", value: "100%", desc: "结论可溯源", target: "100%", ok: true },
];

const CAPABILITIES = [
  { icon: "Checked", title: "多智能体编排", desc: "6 个专业 Agent 协同，含条件路由与专家论证分支", tag: "Agent" },
  { icon: "Search", title: "RAG 混合检索", desc: "BM25 + 向量双路 RRF 融合，PGVector 落库", tag: "RAG" },
  { icon: "MagicStick", title: "MCP 工具服务器", desc: "6 个标准化工具，Agent 与工具解耦", tag: "MCP" },
  { icon: "DataAnalysis", title: "评测体系", desc: "27 条评测集，7 大危大类别 ×4 种难度", tag: "Eval" },
  { icon: "Cpu", title: "全链路 Trace", desc: "每环节耗时/token/拒答次数可追溯", tag: "可观测" },
  { icon: "Lock", title: "证据闸门", desc: "无可靠依据不出具判定，拒绝幻觉", tag: "可靠性" },
];

onMounted(async () => {
  try {
    const [s, list] = await Promise.all([reviewApi.stats(), reviewApi.list({ page_size: 6 })]);
    stats.value = s.data;
    recent.value = list.data.items;
  } catch {
    // 后端未就绪时保持空态，不阻塞渲染
  } finally {
    loading.value = false;
  }
});

function fmtDate(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}
</script>

<template>
  <div class="dashboard">
    <!-- 核心指标 -->
    <el-row :gutter="12" class="stat-row">
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-value">{{ stats.total_reviews }}</div>
          <div class="stat-label">累计审查</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-value danger">{{ stats.hazard_reviews }}</div>
          <div class="stat-label">危大工程</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-value warn">{{ stats.over_scale_reviews }}</div>
          <div class="stat-label">超规模</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-value ok">{{ stats.pass_rate }}%</div>
          <div class="stat-label">一次通过率</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="12" class="main-row">
      <!-- 评测指标 -->
      <el-col :span="14">
        <el-card shadow="never" class="panel">
          <template #header>
            <div class="panel-head">
              <span>检索质量实测指标</span>
              <el-tag size="small" type="info" effect="plain">27 条评测集 · 真实法规语料</el-tag>
            </div>
          </template>
          <div class="metrics">
            <div v-for="m in METRICS" :key="m.name" class="metric">
              <div class="m-value" :class="{ warn: !m.ok }">{{ m.value }}</div>
              <div class="m-name">{{ m.name }}</div>
              <div class="m-desc">{{ m.desc }} · 目标 {{ m.target }}</div>
            </div>
          </div>
        </el-card>

        <!-- 最近审查 -->
        <el-card shadow="never" class="panel mt-12">
          <template #header><span>最近审查</span></template>
          <el-table :data="recent" v-loading="loading" size="small" empty-text="暂无审查记录">
            <el-table-column prop="title" label="任务名称" min-width="180" show-overflow-tooltip />
            <el-table-column label="结论" width="110">
              <template #default="{ row }">
                <el-tag
                  size="small"
                  :type="CONCLUSION_META[row.overall_conclusion || '']?.type || 'info'"
                  effect="plain"
                >
                  {{ CONCLUSION_META[row.overall_conclusion || ""]?.label || "-" }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="评分" width="80">
              <template #default="{ row }">
                <span class="score">{{ row.overall_score ?? "-" }}</span>
              </template>
            </el-table-column>
            <el-table-column label="超规模" width="90">
              <template #default="{ row }">
                <el-tag v-if="row.is_over_scale" size="small" type="warning" effect="plain">
                  超规模
                </el-tag>
                <span v-else class="dim">-</span>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="时间" width="180">
              <template #default="{ row }">
                <span class="dim">{{ fmtDate(row.created_at) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <!-- 能力矩阵 -->
      <el-col :span="10">
        <el-card shadow="never" class="panel">
          <template #header><span>核心能力</span></template>
          <div class="capabilities">
            <div v-for="c in CAPABILITIES" :key="c.title" class="cap">
              <div class="cap-icon">
                <el-icon><component :is="c.icon" /></el-icon>
              </div>
              <div class="cap-body">
                <div class="cap-title">
                  {{ c.title }}
                  <span class="cap-tag">{{ c.tag }}</span>
                </div>
                <div class="cap-desc">{{ c.desc }}</div>
              </div>
            </div>
          </div>
        </el-card>

        <el-card shadow="never" class="panel mt-12 quick-card">
          <template #header><span>快速开始</span></template>
          <el-button type="primary" class="full-btn" @click="router.push('/review')">
            <el-icon><VideoPlay /></el-icon>
            发起合规审查
          </el-button>
          <el-button class="full-btn mt-8" @click="router.push('/regulations')">
            <el-icon><Search /></el-icon>
            法规速查
          </el-button>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped lang="scss">
.dashboard {
  padding: 12px;
}

.stat-row {
  margin-bottom: 12px;
}

.stat-card {
  :deep(.el-card__body) {
    padding: 18px;
    text-align: center;
  }

  .stat-value {
    font-size: 30px;
    font-weight: 600;
    line-height: 1.2;
    font-variant-numeric: tabular-nums;
    color: var(--el-color-primary);

    &.danger {
      color: var(--el-color-danger);
    }
    &.warn {
      color: var(--el-color-warning);
    }
    &.ok {
      color: var(--el-color-success);
    }
  }

  .stat-label {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    margin-top: 4px;
  }
}

.main-row {
  align-items: flex-start;
}

.panel :deep(.el-card__body) {
  padding: 14px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;

  .metric {
    text-align: center;
    padding: 12px 6px;
    background: var(--el-fill-color-light);
    border-radius: 6px;

    .m-value {
      font-size: 21px;
      font-weight: 700;
      color: var(--el-color-success);
      font-variant-numeric: tabular-nums;
      letter-spacing: -0.3px;

      &.warn {
        color: var(--el-color-warning);
      }
    }

    .m-name {
      font-size: 12.5px;
      margin-top: 2px;
      color: var(--el-text-color-primary);
    }

    .m-desc {
      font-size: 10.5px;
      color: var(--el-text-color-secondary);
      margin-top: 2px;
    }
  }
}

.capabilities {
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.cap {
  display: flex;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 8px;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-blank);
  transition: border-color 0.18s, box-shadow 0.18s;

  &:hover {
    border-color: var(--el-color-primary-light-7);
    box-shadow: 0 2px 8px rgba(64, 158, 255, 0.08);
  }

  .cap-icon {
    width: 32px;
    height: 32px;
    border-radius: 8px;
    background: var(--el-color-primary-light-9);
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;

    /* 图标本身必须有尺寸和颜色，否则会渲染成看不见的空盒子 */
    .el-icon {
      font-size: 17px;
      color: var(--el-color-primary);
    }
  }

  .cap-body {
    min-width: 0;
    flex: 1;
  }

  .cap-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--el-text-color-primary);
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .cap-tag {
    font-size: 10px;
    font-weight: 500;
    color: var(--el-color-primary);
    background: var(--el-color-primary-light-9);
    padding: 1px 6px;
    border-radius: 3px;
    letter-spacing: 0.2px;
  }

  .cap-desc {
    font-size: 11.5px;
    color: var(--el-text-color-secondary);
    line-height: 1.55;
    margin-top: 2px;
  }
}

.quick-card .full-btn {
  width: 100%;
  margin-left: 0 !important;
}

.mt-12 {
  margin-top: 12px;
}

.score {
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}

.dim {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>