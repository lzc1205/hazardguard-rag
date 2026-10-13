<script setup lang="ts">
/**
 * 审查记录列表（需求文档 7.4.7）。
 *
 * 之前的「最近审查」只在概览页显示 6 条，
 * 没有独立页面就无法翻页、筛选、批量查看——
 * 审查记录是系统的主要产出物，必须有正式入口。
 */
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { reviewApi, projectApi, documentApi, type ProjectItem } from "@/api";
import { CONCLUSION_META } from "@/utils/constants";

const router = useRouter();
const loading = ref(false);
const items = ref<any[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = 20;

/**
 * 视图切换：审查记录 / 报告。
 *
 * 需求文档 7.4.7 与 7.4.9 分成两页，但数据源完全相同（都是 reviews 表），
 * 做两个页面只是让用户在两个几乎一样的表格间来回跳。
 * 所以合并成一页、两个视图：
 *  - 审查视图：按审查维度看（意见数、拒答、成本）
 *  - 报告视图：按交付物看（结论、评分、可导出）
 */
const view = ref<"review" | "report">("review");

const projects = ref<ProjectItem[]>([]);
const docs = ref<any[]>([]);

const filters = ref({
  project_id: "",
  document_id: "",
  conclusion: "",
  keyword: "",
  only_over_scale: false,
});

const stats = computed(() => ({
  total: total.value,
  hazard: items.value.filter((r) => r.is_hazard_project).length,
  overScale: items.value.filter((r) => r.is_over_scale).length,
  failed: items.value.filter((r) => r.status === "failed").length,
  avgScore: items.value.length
    ? Math.round(
        items.value.reduce((s, r) => s + (r.overall_score || 0), 0) /
          items.value.length,
      )
    : 0,
}));

/** 报告视图：按结论分组，直观看出「多少份通过、多少要改」 */
const reportGroups = computed(() => {
  const g: Record<string, any[]> = {};
  items.value.forEach((r) => {
    const k = r.overall_conclusion || "pending";
    (g[k] ||= []).push(r);
  });
  return Object.entries(g).map(([key, rows]) => ({
    key,
    label: CONCLUSION_META[key]?.label || "待定",
    type: CONCLUSION_META[key]?.type || "info",
    rows,
    avg: rows.length
      ? Math.round(rows.reduce((s, r) => s + (r.overall_score || 0), 0) / rows.length)
      : 0,
  }));
});

/** 结论配色：报告视图的分组标题要用 */
const CONCLUSION_COLOR: Record<string, string> = {
  pass: "#276749",
  pass_with_modification: "#B7791F",
  fail: "#C0392B",
  pending: "#718096",
};

function fmtTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

async function load() {
  loading.value = true;
  try {
    const res = await reviewApi.list({
      page: page.value,
      page_size: pageSize,
      project_id: filters.value.project_id || undefined,
      document_id: filters.value.document_id || undefined,
      overall_conclusion: filters.value.conclusion || undefined,
      keyword: filters.value.keyword || undefined,
      is_over_scale: filters.value.only_over_scale ? "true" : undefined,
    } as any);
    items.value = res.data.items || [];
    total.value = res.data.total || 0;
  } catch {
    ElMessage.error("加载审查记录失败");
  } finally {
    loading.value = false;
  }
}

async function loadFilters() {
  try {
    const [p, d] = await Promise.all([
      projectApi.list({ page_size: 100 }),
      documentApi.list({ page_size: 100 }),
    ]);
    projects.value = p.data.items || [];
    docs.value = d.data.items || [];
  } catch {
    // 筛选项加载失败不阻塞列表
  }
}

function reset() {
  filters.value = {
    project_id: "",
    document_id: "",
    conclusion: "",
    keyword: "",
    only_over_scale: false,
  };
  page.value = 1;
  load();
}

/** 项目名/文档名反查，避免表格里只显示 ID */
const projectName = computed(() => {
  const m: Record<string, string> = {};
  projects.value.forEach((p) => (m[p.id] = p.name));
  return m;
});
const docName = computed(() => {
  const m: Record<string, string> = {};
  docs.value.forEach((d) => (m[d.id] = d.title));
  return m;
});

function openDetail(row: any) {
  router.push(`/review?id=${row.id}`);
}

function openReport(row: any) {
  router.push(`/reports/${row.id}`);
}

async function remove(row: any) {
  try {
    await ElMessageBox.confirm(
      `确认删除审查记录「${row.title}」？该操作不可撤销。`,
      "删除审查记录",
      { type: "warning", confirmButtonText: "确认删除", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  try {
    await reviewApi.remove(row.id);
    ElMessage.success("已删除");
    load();
  } catch {
    ElMessage.error("删除失败");
  }
}

onMounted(async () => {
  await Promise.all([load(), loadFilters()]);
});
</script>

<template>
  <div class="review-page">
    <!-- 视图切换：审查记录 / 报告 -->
    <div class="view-switch">
      <el-radio-group v-model="view" size="default">
        <el-radio-button value="review">
          <el-icon><Tickets /></el-icon> 审查记录
        </el-radio-button>
        <el-radio-button value="report">
          <el-icon><Document /></el-icon> 报告
        </el-radio-button>
      </el-radio-group>
      <span class="vs-tip">
        {{ view === "review" ? "按审查过程查看：意见、拒答、成本" : "按交付物查看：结论、评分、导出" }}
      </span>
    </div>

    <!-- 统计条 -->
    <el-row :gutter="10" class="stat-row">
      <el-col :span="5">
        <div class="stat-box"><div class="s-value">{{ total }}</div><div class="s-label">{{ view === "review" ? "审查总数" : "报告总数" }}</div></div>
      </el-col>
      <el-col :span="5">
        <div class="stat-box">
          <div class="s-value">{{ stats.hazard }}</div><div class="s-label">本页危大</div>
        </div>
      </el-col>
      <el-col :span="5">
        <div class="stat-box warn">
          <div class="s-value">{{ stats.overScale }}</div><div class="s-label">本页超规模</div>
        </div>
      </el-col>
      <el-col :span="5">
        <div class="stat-box" :class="{ bad: stats.failed > 0 }">
          <div class="s-value">{{ view === "review" ? stats.avgScore : stats.failed }}</div>
          <div class="s-label">{{ view === "review" ? "本页均分" : "失败记录" }}</div>
        </div>
      </el-col>
      <el-col :span="4">
        <el-button type="primary" size="small" class="new-btn" @click="router.push('/review')">
          <el-icon><Plus /></el-icon>发起审查
        </el-button>
      </el-col>
    </el-row>

    <!-- 筛选条 -->
    <el-card shadow="never" class="toolbar">
      <div class="tb-row">
        <el-input
          v-model="filters.keyword"
          placeholder="搜索审查标题"
          clearable
          size="small"
          style="width: 190px"
          @keyup.enter="() => { page = 1; load(); }"
          @clear="() => { page = 1; load(); }"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>

        <el-select
          v-model="filters.project_id"
          placeholder="全部项目"
          clearable
          size="small"
          style="width: 170px"
          @change="() => { page = 1; load(); }"
        >
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>

        <el-select
          v-model="filters.document_id"
          placeholder="全部文档"
          clearable
          size="small"
          style="width: 180px"
          @change="() => { page = 1; load(); }"
        >
          <el-option v-for="d in docs" :key="d.id" :label="d.title" :value="d.id" />
        </el-select>

        <el-select
          v-model="filters.conclusion"
          placeholder="全部结论"
          clearable
          size="small"
          style="width: 130px"
          @change="() => { page = 1; load(); }"
        >
          <el-option label="通过" value="pass" />
          <el-option label="修改后通过" value="pass_with_modification" />
          <el-option label="不通过" value="fail" />
        </el-select>

        <el-checkbox
          v-model="filters.only_over_scale"
          size="small"
          @change="() => { page = 1; load(); }"
        >
          仅看超规模
        </el-checkbox>

        <el-button size="small" @click="reset">
          <el-icon><RefreshLeft /></el-icon>重置
        </el-button>
      </div>
    </el-card>

    <!-- 记录表（审查视图） -->
    <el-card v-if="view === 'review'" shadow="never" class="table-card">
      <el-table :data="items" v-loading="loading" size="small" empty-text="没有匹配的审查记录">
        <el-table-column label="审查项" min-width="230">
          <template #default="{ row }">
            <div class="rv-title" @click="openDetail(row)">{{ row.title }}</div>
            <div class="rv-sub">
              <span v-if="row.project_id">{{ projectName[row.project_id] || "-" }}</span>
              <span v-if="row.document_id">· {{ docName[row.document_id] || "-" }}</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="危大类别" width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.hazard_categories?.length" class="dim">
              {{ row.hazard_categories.join("、") }}
            </span>
            <span v-else class="dim">非危大</span>
          </template>
        </el-table-column>

        <el-table-column label="结论" width="110">
          <template #default="{ row }">
            <el-tag
              :type="CONCLUSION_META[row.overall_conclusion || '']?.type || 'info'"
              size="small"
              effect="plain"
            >
              {{ CONCLUSION_META[row.overall_conclusion || ""]?.label || "-" }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="评分" width="76" sortable :sort-by="(r: any) => r.overall_score || 0">
          <template #default="{ row }">
            <span class="score">{{ row.overall_score ?? "-" }}</span>
          </template>
        </el-table-column>

        <el-table-column label="超规模" width="88">
          <template #default="{ row }">
            <el-tag v-if="row.is_over_scale" type="danger" size="small" effect="plain">超规模</el-tag>
            <span v-else class="dim">-</span>
          </template>
        </el-table-column>

        <el-table-column label="成本" width="88">
          <template #default="{ row }">
            <span class="dim">¥{{ (row.cost_estimate ?? 0).toFixed(4) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="时间" width="160">
          <template #default="{ row }">
            <span class="dim">{{ fmtTime(row.created_at) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">查看</el-button>
            <el-button link type="primary" size="small" @click="openReport(row)">报告</el-button>
            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager" v-if="total > pageSize">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="prev, pager, next, total"
          size="small"
          @current-change="load"
        />
      </div>
    </el-card>

    <!-- 报告视图：按结论分组 -->
    <div v-else v-loading="loading" class="report-groups">
      <el-empty
        v-if="!loading && items.length === 0"
        description="还没有审查报告"
        class="empty"
      />

      <el-card
        v-for="g in reportGroups"
        :key="g.key"
        shadow="never"
        class="group-card"
      >
        <template #header>
          <div class="group-head">
            <span class="gh-bar" :style="{ background: CONCLUSION_COLOR[g.key] }" />
            <span class="gh-label">{{ g.label }}</span>
            <el-tag size="small" effect="plain">{{ g.rows.length }} 份</el-tag>
            <span class="gh-avg">均分 {{ g.avg }}</span>
          </div>
        </template>

        <div class="report-rows">
          <div
            v-for="r in g.rows"
            :key="r.id"
            class="report-row"
            @click="openReport(r)"
          >
            <div class="rr-main">
              <div class="rr-title">{{ r.title }}</div>
              <div class="rr-meta">
                <span>{{ fmtTime(r.created_at) }}</span>
                <span v-if="r.project_id">· {{ projectName[r.project_id] || "-" }}</span>
                <span v-if="r.is_over_scale" class="rr-over">· 超规模</span>
              </div>
            </div>
            <div class="rr-score">
              <span class="rs-num">{{ r.overall_score ?? "-" }}</span>
              <span class="rs-label">分</span>
            </div>
            <div class="rr-actions" @click.stop>
              <el-button link type="primary" size="small" @click="openReport(r)">
                查看报告
              </el-button>
              <el-button link type="danger" size="small" @click="remove(r)">删除</el-button>
            </div>
          </div>
        </div>
      </el-card>

      <div class="pager" v-if="total > pageSize">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="prev, pager, next, total"
          size="small"
          @current-change="load"
        />
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.review-page {
  padding: 12px;
}

.view-switch {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.vs-tip {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.stat-row {
  margin-bottom: 10px;
}

.stat-box {
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 11px 12px;
  text-align: center;

  .s-value {
    font-size: 20px;
    font-weight: 500;
    color: var(--el-color-primary);
    font-variant-numeric: tabular-nums;
    line-height: 1.25;
  }

  .s-label {
    font-size: 11.5px;
    color: var(--el-text-color-secondary);
  }

  &.warn .s-value {
    color: var(--el-color-warning);
  }

  &.bad .s-value {
    color: var(--el-color-danger);
  }
}

/* 报告视图：按结论分组的卡片 */
.report-groups {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 120px;
}

.group-card :deep(.el-card__body) {
  padding: 0;
}

.group-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.gh-bar {
  width: 3px;
  height: 14px;
  border-radius: 2px;
}

.gh-label {
  font-size: 13.5px;
  font-weight: 500;
  color: var(--el-text-color-primary);
}

.gh-avg {
  margin-left: auto;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.report-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  cursor: pointer;
  transition: background 0.15s;

  &:last-child {
    border-bottom: none;
  }

  &:hover {
    background: var(--el-fill-color-light);
  }
}

.rr-main {
  flex: 1;
  min-width: 0;
}

.rr-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rr-meta {
  display: flex;
  gap: 6px;
  font-size: 11.5px;
  color: var(--el-text-color-placeholder);
  margin-top: 2px;

  .rr-over {
    color: var(--el-color-danger);
  }
}

.rr-score {
  display: flex;
  align-items: baseline;
  gap: 2px;
  width: 58px;
  justify-content: flex-end;

  .rs-num {
    font-size: 17px;
    font-weight: 500;
    color: var(--el-color-primary);
    font-variant-numeric: tabular-nums;
  }

  .rs-label {
    font-size: 11px;
    color: var(--el-text-color-placeholder);
  }
}

.rr-actions {
  width: 130px;
  text-align: right;
}

.empty {
  padding: 40px 0;
}

.new-btn {
  width: 100%;
  height: 52px;
}

.toolbar {
  margin-bottom: 12px;

  :deep(.el-card__body) {
    padding: 10px 14px;
  }
}

.tb-row {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
}

.table-card :deep(.el-card__body) {
  padding: 0;
}

.rv-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;

  &:hover {
    color: var(--el-color-primary);
  }
}

.rv-sub {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.score {
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.dim {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.pager {
  display: flex;
  justify-content: flex-end;
  padding: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}
</style>