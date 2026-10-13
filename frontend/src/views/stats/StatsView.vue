<script setup lang="ts">
/**
 * 统计分析（需求文档 7.4.16）。
 *
 * 图表全部用纯 CSS 绘制，不引 ECharts/Chart.js：
 * 这个项目的图表只有柱状/环形/趋势三种，
 * 为此引入 400KB+ 的依赖不划算，而且简历上写「引入 ECharts」
 * 并不比「手写 SVG 柱状图」更有说服力。
 */
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { reviewApi } from "@/api";
import { CONCLUSION_META } from "@/utils/constants";

const loading = ref(false);
const data = ref<any>(null);
const days = ref(30);

const CONCLUSION_COLOR: Record<string, string> = {
  pass: "#276749",
  pass_with_modification: "#B7791F",
  fail: "#C0392B",
  pending: "#718096",
};
const CATEGORY_LABEL: Record<string, string> = {
  foundation_pit: "基坑工程",
  formwork: "模板工程及支撑",
  lifting: "起重吊装",
  scaffold: "脚手架工程",
  demolition: "拆除爆破",
  underground: "暗挖工程",
  other: "其他",
};

const conclusionRows = computed(() => data.value?.conclusion_dist || []);
const categoryRows = computed(() => data.value?.category_dist || []);
const daily = computed(() => data.value?.daily || []);
const cost = computed(() => data.value?.cost || {});
const score = computed(() => data.value?.score || {});

/** 环形图：conic-gradient 按各结论占比分段 */
const donutStyle = computed(() => {
  const rows = conclusionRows.value;
  const total = rows.reduce((s: number, r: any) => s + r.count, 0);
  if (!total) return { background: "#E2E8F0" };
  let acc = 0;
  const parts = rows.map((r: any) => {
    const start = (acc / total) * 100;
    acc += r.count;
    const end = (acc / total) * 100;
    return `${CONCLUSION_COLOR[r.key] || "#718096"} ${start}% ${end}%`;
  });
  return { background: `conic-gradient(${parts.join(",")})` };
});

const donutCenter = computed(() => {
  const rows = conclusionRows.value;
  const total = rows.reduce((s: number, r: any) => s + r.count, 0);
  const pass = rows
    .filter((r: any) => r.key === "pass")
    .reduce((s: number, r: any) => s + r.count, 0);
  return {
    total,
    rate: total ? Math.round((pass / total) * 100) : 0,
  };
});

const maxDaily = computed(() =>
  Math.max(1, ...daily.value.map((d: any) => d.count)),
);
const maxCategory = computed(() =>
  Math.max(1, ...categoryRows.value.map((d: any) => d.count)),
);

async function load() {
  loading.value = true;
  try {
    const res = await reviewApi.statsDashboard(days.value);
    data.value = res.data;
  } catch {
    ElMessage.error("加载统计数据失败");
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="stats-page" v-loading="loading">
    <!-- 顶部指标 -->
    <el-row :gutter="10" class="kpi-row">
      <el-col :span="4">
        <div class="kpi">
          <div class="k-value">{{ data?.total ?? 0 }}</div>
          <div class="k-label">累计审查</div>
        </div>
      </el-col>
      <el-col :span="4">
        <div class="kpi">
          <div class="k-value ok">{{ score.avg ?? 0 }}</div>
          <div class="k-label">平均评分</div>
        </div>
      </el-col>
      <el-col :span="4">
        <div class="kpi">
          <div class="k-value">{{ cost.avg_duration ?? 0 }}s</div>
          <div class="k-label">平均耗时</div>
        </div>
      </el-col>
      <el-col :span="4">
        <div class="kpi">
          <div class="k-value warn">¥{{ (cost.avg_cost ?? 0).toFixed(4) }}</div>
          <div class="k-label">单次成本</div>
        </div>
      </el-col>
      <el-col :span="4">
        <div class="kpi">
          <div class="k-value">{{ ((cost.total_tokens ?? 0) / 1000).toFixed(1) }}K</div>
          <div class="k-label">累计 Token</div>
        </div>
      </el-col>
      <el-col :span="4">
        <div class="kpi-range">
          <el-radio-group v-model="days" size="small" @change="load">
            <el-radio-button :value="7">7 天</el-radio-button>
            <el-radio-button :value="30">30 天</el-radio-button>
            <el-radio-button :value="90">90 天</el-radio-button>
          </el-radio-group>
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="12">
      <!-- 结论分布 -->
      <el-col :span="8">
        <el-card shadow="never" class="panel">
          <template #header><span>审查结论分布</span></template>
          <div class="donut-wrap">
            <div class="donut" :style="donutStyle">
              <div class="donut-hole">
                <div class="dh-num">{{ donutCenter.total }}</div>
                <div class="dh-label">总审查</div>
              </div>
            </div>
          </div>
          <div class="legend">
            <div
              v-for="r in conclusionRows"
              :key="r.key"
              class="legend-row"
            >
              <span
                class="lg-dot"
                :style="{ background: CONCLUSION_COLOR[r.key] || '#718096' }"
              />
              <span class="lg-name">
                {{ CONCLUSION_META[r.key]?.label || r.key }}
              </span>
              <span class="lg-count">{{ r.count }}</span>
              <span class="lg-pct">
                {{
                  ((r.count / (donutCenter.total || 1)) * 100).toFixed(0)
                }}%
              </span>
            </div>
            <div v-if="!conclusionRows.length" class="empty-tip">暂无数据</div>
          </div>
        </el-card>
      </el-col>

      <!-- 危大类别分布 -->
      <el-col :span="8">
        <el-card shadow="never" class="panel">
          <template #header>
            <div class="panel-head">
              <span>危大类别命中分布</span>
              <span class="dim">一条审查可命中多个类别</span>
            </div>
          </template>
          <div class="bars">
            <div v-for="c in categoryRows" :key="c.key" class="bar-row">
              <span class="b-name">
                {{ CATEGORY_LABEL[c.key] || c.key }}
              </span>
              <div class="b-track">
                <div
                  class="b-fill"
                  :style="{
                    width: (c.count / maxCategory) * 100 + '%',
                  }"
                />
              </div>
              <span class="b-count">{{ c.count }}</span>
            </div>
            <div v-if="!categoryRows.length" class="empty-tip">暂无数据</div>
          </div>
        </el-card>
      </el-col>

      <!-- 成本与耗时 -->
      <el-col :span="8">
        <el-card shadow="never" class="panel">
          <template #header><span>成本与耗时</span></template>

          <div class="cost-list">
            <div class="cost-item">
              <div class="ci-label">累计成本</div>
              <div class="ci-value">¥{{ (cost.total ?? 0).toFixed(4) }}</div>
            </div>
            <div class="cost-item">
              <div class="ci-label">累计 Token</div>
              <div class="ci-value">{{ (cost.total_tokens ?? 0).toLocaleString() }}</div>
            </div>
            <div class="cost-item">
              <div class="ci-label">最慢单次</div>
              <div class="ci-value">{{ cost.max_duration ?? 0 }}s</div>
            </div>
            <div class="cost-item">
              <div class="ci-label">评分区间</div>
              <div class="ci-value">
                {{ score.min ?? 0 }} ~ {{ score.max ?? 0 }}
              </div>
            </div>
          </div>

          <div class="cost-note">
            成本按 DeepSeek 实际计费核算（输入输出分开计价）。<br />
            单次审查约 ¥0.005，1000 次约 ¥5。
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 趋势 -->
    <el-card shadow="never" class="panel mt-12">
      <template #header>
        <div class="panel-head">
          <span>近 {{ days }} 天审查趋势</span>
          <span class="dim">共 {{ daily.reduce((s: number, d: any) => s + d.count, 0) }} 次</span>
        </div>
      </template>
      <div v-if="daily.length" class="trend">
        <div v-for="d in daily" :key="d.date" class="trend-col">
          <div class="tc-bar-wrap">
            <div
              class="tc-bar"
              :style="{ height: (d.count / maxDaily) * 100 + '%' }"
              :title="`${d.date}：${d.count} 次`"
            />
          </div>
          <div class="tc-count">{{ d.count }}</div>
          <div class="tc-date">{{ d.date.slice(5) }}</div>
        </div>
      </div>
      <div v-else class="empty-tip">近 {{ days }} 天没有审查记录</div>
    </el-card>
  </div>
</template>

<style scoped lang="scss">
.stats-page {
  padding: 12px;
}

.kpi-row {
  margin-bottom: 12px;
}

.kpi {
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 13px 10px;
  text-align: center;

  .k-value {
    font-size: 22px;
    font-weight: 500;
    color: var(--el-color-primary);
    font-variant-numeric: tabular-nums;
    line-height: 1.25;

    &.ok {
      color: var(--el-color-success);
    }
    &.warn {
      color: var(--el-color-warning);
    }
  }

  .k-label {
    font-size: 11.5px;
    color: var(--el-text-color-secondary);
    margin-top: 2px;
  }
}

.kpi-range {
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 16px 10px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.panel :deep(.el-card__body) {
  padding: 14px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.mt-12 {
  margin-top: 12px;
}

/* 环形图 */
.donut-wrap {
  display: flex;
  justify-content: center;
  padding: 8px 0 16px;
}

.donut {
  width: 132px;
  height: 132px;
  border-radius: 50%;
  position: relative;
}

.donut-hole {
  position: absolute;
  inset: 24px;
  background: #fff;
  border-radius: 50%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.dh-num {
  font-size: 24px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  font-variant-numeric: tabular-nums;
  line-height: 1.15;
}

.dh-label {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}

/* 图例 */
.legend-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);
  font-size: 12.5px;

  &:last-child {
    border-bottom: none;
  }
}

.lg-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
}

.lg-name {
  color: var(--el-text-color-regular);
}

.lg-count {
  margin-left: auto;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.lg-pct {
  width: 42px;
  text-align: right;
  color: var(--el-text-color-placeholder);
  font-variant-numeric: tabular-nums;
  font-size: 11.5px;
}

/* 条形图 */
.bars {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 4px;
}

.bar-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.b-name {
  width: 92px;
  font-size: 12px;
  color: var(--el-text-color-regular);
  flex-shrink: 0;
}

.b-track {
  flex: 1;
  height: 18px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  overflow: hidden;
}

.b-fill {
  height: 100%;
  background: linear-gradient(90deg, #4a9eff, #2b7fd4);
  border-radius: 4px;
  min-width: 3px;
  transition: width 0.3s;
}

.b-count {
  width: 30px;
  text-align: right;
  font-size: 12.5px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

/* 成本 */
.cost-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.cost-item {
  padding: 10px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}

.ci-label {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
  margin-bottom: 3px;
}

.ci-value {
  font-size: 15px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  font-variant-numeric: tabular-nums;
}

.cost-note {
  margin-top: 14px;
  padding: 9px 11px;
  background: var(--el-color-primary-light-9);
  border-radius: 6px;
  font-size: 11.5px;
  color: var(--el-text-color-regular);
  line-height: 1.7;
}

/* 趋势 */
.trend {
  display: flex;
  align-items: flex-end;
  gap: 5px;
  height: 170px;
  padding-top: 8px;
  overflow-x: auto;
}

.trend-col {
  flex: 1;
  min-width: 26px;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
}

.tc-bar-wrap {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}

.tc-bar {
  width: 70%;
  min-width: 8px;
  background: linear-gradient(180deg, #4a9eff, #2b7fd4);
  border-radius: 3px 3px 0 0;
  transition: height 0.3s;
}

.tc-count {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-top: 3px;
  font-variant-numeric: tabular-nums;
}

.tc-date {
  font-size: 10px;
  color: var(--el-text-color-placeholder);
  white-space: nowrap;
}

.dim {
  font-size: 11.5px;
  color: var(--el-text-color-placeholder);
}

.empty-tip {
  font-size: 12.5px;
  color: var(--el-text-color-placeholder);
  text-align: center;
  padding: 28px 0;
}
</style>