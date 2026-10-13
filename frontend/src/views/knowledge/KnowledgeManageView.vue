<script setup lang="ts">
/**
 * 知识库管理（需求文档 7.4.11~7.4.14）。
 *
 * 原文档拆成 4 页（仪表盘/条目列表/条目详情/构建），
 * 但它们共用同一份数据（entries 表），拆开只是让用户在四个页签间跳。
 * 所以合成一页三 Tab：
 *   概览   —— 索引健康度 + 类别分布
 *   条目   —— 列表 + 详情抽屉（替代独立详情页）
 *   构建   —— 向量重建 / 索引重载 / 评测运行
 */
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { knowledgeApi, vectorApi, promptApi } from "@/api";

const tab = ref<"overview" | "entries" | "build">("overview");
const loading = ref(false);

/* ---------------- 概览 ---------------- */
const vectorStats = ref<any>(null);
const categories = ref<any[]>([]);
const llmStatus = ref<any>(null);
const promptCount = ref(0);
const mandatoryCount = ref(0);
const entriesByCategory = ref<Record<string, number>>({});

/* ---------------- 条目 ---------------- */
const entries = ref<any[]>([]);
const entryPage = ref(1);
/** 条目总数：概览与列表共用同一份分页结果，避免两处状态不同步 */
const entryTotal = ref(0);
const entryFilters = ref({ category: "", keyword: "", only_mandatory: false });
const drawerVisible = ref(false);
const currentEntry = ref<any>(null);
const entryContent = ref("");

/* ---------------- 构建 ---------------- */
const rebuilding = ref(false);
const reloading = ref(false);
const runningEval = ref(false);
const buildLog = ref<Array<{ time: string; text: string; type: string }>>([]);

const CATEGORY_LABEL: Record<string, string> = {
  foundation_pit: "基坑工程",
  formwork: "模板工程及支撑体系",
  lifting: "起重吊装及安装拆卸",
  scaffold: "脚手架工程",
  demolition: "拆除爆破工程",
  underground: "暗挖工程",
  other: "其他",
};

const CATEGORY_COLOR: Record<string, string> = {
  foundation_pit: "#409eff",
  formwork: "#67c23a",
  lifting: "#e6a23c",
  scaffold: "#f56c6c",
  demolition: "#909399",
  underground: "#9254de",
  other: "#8c8c8c",
};

const healthStatus = computed(() => {
  const c = vectorStats.value?.coverage ?? 0;
  if (c >= 100) return { label: "索引完整", type: "success" };
  if (c >= 50) return { label: "部分未索引", type: "warning" };
  return { label: "索引缺失", type: "danger" };
});

const maxCategoryCount = computed(() =>
  Math.max(1, ...Object.values(entriesByCategory.value)),
);

function fmtTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

function log(text: string, type: "ok" | "warn" | "err" = "ok") {
  buildLog.value.unshift({
    time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
    text,
    type,
  });
}

/* ---------------- 加载 ---------------- */

async function loadOverview() {
  loading.value = true;
  try {
    const [v, c, l, p] = await Promise.all([
      vectorApi.stats(),
      knowledgeApi.categories(),
      promptApi.llmStatus(),
      promptApi.list(),
    ]);
    vectorStats.value = v.data;
    // 后端 key 是 Python 枚举，序列化成字符串；
    // 但保险起见做一次归一化，避免 undefined 落到 key 上
    categories.value = (c.data || []).map((x: any) => ({
      ...x,
      key: typeof x.key === "string" ? x.key : String(x.key),
    }));
    llmStatus.value = l.data;
    promptCount.value = (p.data || []).length;

    // 条目统计（一次拉全量只用于统计分类分布，条目页有分页）
    const list = await knowledgeApi.listEntries({ page_size: 100 });
    const rows = list.data.items || [];
    entryTotal.value = list.data.total || 0;
    mandatoryCount.value = rows.filter((e: any) => e.is_mandatory).length;
    const dist: Record<string, number> = {};
    rows.forEach((e: any) => {
      const k = e.category || "other";
      dist[k] = (dist[k] || 0) + 1;
    });
    entriesByCategory.value = dist;
  } catch {
    ElMessage.error("加载知识库概览失败");
  } finally {
    loading.value = false;
  }
}

async function loadEntries() {
  loading.value = true;
  try {
    const res = await knowledgeApi.listEntries({
      page: entryPage.value,
      page_size: 20,
      category: entryFilters.value.category || undefined,
      keyword: entryFilters.value.keyword || undefined,
      only_mandatory: entryFilters.value.only_mandatory ? "true" : undefined,
    } as any);
    entries.value = res.data.items || [];
    entryTotal.value = res.data.total || 0;
  } catch {
    ElMessage.error("加载条目失败");
  } finally {
    loading.value = false;
  }
}

async function openDetail(row: any) {
  currentEntry.value = row;
  entryContent.value = "";
  drawerVisible.value = true;

  // 条目表不含条文正文，按规范号取回该规范全部条款再定位到当前条款
  if (!row.regulation_code) return;
  try {
    const res = await knowledgeApi.clauses(row.regulation_code);
    const items = res.data.items || [];
    const want = (row.clause_number || "").replace(/^第/, "").replace(/条$/, "");
    const hit =
      items.find((i) => (i.clause_no || "").replace(/^第/, "").replace(/条$/, "") === want) ||
      items[0];
    if (hit) {
      entryContent.value = hit.content || "";
      // 详情页展示完整出处，不只是裸条款内容
      entryContent.value = `${hit.citation || ""}\n\n${hit.content || ""}`;
    }
  } catch {
    entryContent.value = "";
  }
}

async function removeEntry(row: any) {
  try {
    await ElMessageBox.confirm(
      `确认删除条目「${row.title}」？删除后需重新构建索引才会生效。`,
      "删除法规条目",
      { type: "warning", confirmButtonText: "确认删除", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  try {
    await knowledgeApi.removeEntry(row.id);
    ElMessage.success("已删除，请点击「重新加载索引」使其生效");
    loadEntries();
    loadOverview();
  } catch {
    ElMessage.error("删除失败");
  }
}

/* ---------------- 构建动作 ---------------- */

async function doReindex() {
  rebuilding.value = true;
  log("开始重建向量索引…");
  try {
    const res = await vectorApi.reindex();
    const d = res.data || {};
    log(
      `完成：条目 ${d.entries_created ?? 0} 条，分块 ${d.chunks_indexed ?? 0} 块，维度 ${d.dimension ?? "-"}`,
    );
    ElMessage.success("向量索引重建完成");
    loadOverview();
  } catch {
    log("重建失败，请查看后端日志", "err");
    ElMessage.error("重建失败");
  } finally {
    rebuilding.value = false;
  }
}

async function doReload() {
  reloading.value = true;
  log("重新加载 BM25 内存索引…");
  try {
    const res = await knowledgeApi.reload();
    const d = res.data || {};
    log(`索引重载完成，当前 ${d.chunk_count ?? 0} 个分块`);
    ElMessage.success("索引已重载");
  } catch {
    log("索引重载失败", "err");
    ElMessage.error("重载失败");
  } finally {
    reloading.value = false;
  }
}

async function doEval() {
  runningEval.value = true;
  log("开始运行评测集（27 条）…");
  try {
    const res = await knowledgeApi.runEval({ retriever: "real" });
    const m = res.data?.metrics || res.data || {};
    log(
      `评测完成：Recall@5 ${m.recall_at_5 ?? "-"}，命中 ${m.hit_at_5 ?? "-"}，` +
        `引用正确率 ${m.citation_accuracy ?? "-"}`,
    );
    ElMessage.success("评测完成");
  } catch {
    log("评测失败", "err");
    ElMessage.error("评测失败");
  } finally {
    runningEval.value = false;
  }
}

function switchTab(name: any) {
  tab.value = name;
  if (name === "entries" && entries.value.length === 0) loadEntries();
}

onMounted(loadOverview);
</script>

<template>
  <div class="kb-page">
    <el-tabs v-model="tab" class="kb-tabs" @tab-change="switchTab">
      <!-- ============ 概览 ============ -->
      <el-tab-pane label="概览" name="overview">
        <el-row :gutter="10" class="stat-row">
          <el-col :span="6">
            <div class="stat-box">
              <div class="s-value">{{ entryTotal }}</div>
              <div class="s-label">法规条目</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="stat-box">
              <div class="s-value warn">{{ mandatoryCount }}</div>
              <div class="s-label">强制性条文</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="stat-box">
              <div class="s-value">
                {{ vectorStats?.coverage ?? 0 }}%
              </div>
              <div class="s-label">向量索引覆盖</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="stat-box">
              <div class="s-value">{{ promptCount }}</div>
              <div class="s-label">Prompt 模板</div>
            </div>
          </el-col>
        </el-row>

        <el-row :gutter="12">
          <!-- 索引健康度 -->
          <el-col :span="10">
            <el-card shadow="never" class="panel">
              <template #header>
                <div class="panel-head">
                  <span>索引健康度</span>
                  <el-tag :type="healthStatus.type" size="small" effect="plain">
                    {{ healthStatus.label }}
                  </el-tag>
                </div>
              </template>

              <div v-if="vectorStats" class="health">
                <div class="h-row">
                  <span class="h-label">分块总数</span>
                  <span class="h-value">{{ vectorStats.total_chunks }}</span>
                </div>
                <div class="h-row">
                  <span class="h-label">已向量化</span>
                  <span class="h-value">{{ vectorStats.indexed_chunks }}</span>
                </div>
                <div class="h-row">
                  <span class="h-label">向量维度</span>
                  <span class="h-value">{{ vectorStats.dimension }}</span>
                </div>
                <div class="h-row">
                  <span class="h-label">Embedding 模型</span>
                  <span class="h-value small">
                    {{ Object.keys(vectorStats.models || {}).join(", ") || "-" }}
                  </span>
                </div>
                <el-progress
                  :percentage="vectorStats.coverage ?? 0"
                  :stroke-width="8"
                  class="mt-10"
                />
              </div>
              <div v-else class="empty-tip">正在加载…</div>
            </el-card>

            <el-card shadow="never" class="panel mt-12">
              <template #header><span>运行环境</span></template>
              <div class="env-row">
                <span class="env-label">LLM 模式</span>
                <el-tag
                  :type="llmStatus?.available ? 'success' : 'info'"
                  size="small"
                  effect="plain"
                >
                  {{ llmStatus?.available ? llmStatus.model : "规则模式（未配置 Key）" }}
                </el-tag>
              </div>
              <div class="env-row">
                <span class="env-label">检索方式</span>
                <span class="env-val">BM25 + 字符向量 + PGVector 三路融合</span>
              </div>
              <div class="env-row">
                <span class="env-label">拒答策略</span>
                <span class="env-val">无依据不出具判定（证据闸门）</span>
              </div>
            </el-card>
          </el-col>

          <!-- 类别分布 -->
          <el-col :span="14">
            <el-card shadow="never" class="panel">
              <template #header>
                <div class="panel-head">
                  <span>七大危大类别条目分布</span>
                  <el-button link type="primary" size="small" @click="tab = 'entries'">
                    查看条目
                  </el-button>
                </div>
              </template>

              <div class="dist">
                <div v-for="c in categories" :key="c.key" class="dist-row">
                  <span class="d-name">{{ c.name || CATEGORY_LABEL[c.key] || c.key }}</span>
                  <div class="d-bar-wrap">
                    <div
                      class="d-bar"
                      :style="{
                        width:
                          ((entriesByCategory[c.key] || 0) / maxCategoryCount) * 100 + '%',
                        background: CATEGORY_COLOR[c.key] || '#8c8c8c',
                      }"
                    />
                  </div>
                  <span class="d-count">{{ entriesByCategory[c.key] || 0 }}</span>
                </div>
                <div v-if="!categories.length" class="empty-tip">正在加载类别…</div>
              </div>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ============ 条目 ============ -->
      <el-tab-pane label="法规条目" name="entries">
        <el-card shadow="never" class="toolbar">
          <div class="tb-row">
            <el-input
              v-model="entryFilters.keyword"
              placeholder="搜索标题/规范号/条款号"
              clearable
              size="small"
              style="width: 220px"
              @keyup.enter="() => { entryPage = 1; loadEntries(); }"
              @clear="() => { entryPage = 1; loadEntries(); }"
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <el-select
              v-model="entryFilters.category"
              placeholder="全部类别"
              clearable
              size="small"
              style="width: 170px"
              @change="() => { entryPage = 1; loadEntries(); }"
            >
              <el-option
                v-for="c in categories"
                :key="c.key"
                :label="c.name || CATEGORY_LABEL[c.key] || c.key"
                :value="c.key"
              />
            </el-select>
            <el-checkbox
              v-model="entryFilters.only_mandatory"
              size="small"
              @change="() => { entryPage = 1; loadEntries(); }"
            >
              仅看强制性条文
            </el-checkbox>
            <el-button size="small" @click="() => { entryPage = 1; loadEntries(); }">
              <el-icon><Search /></el-icon>查询
            </el-button>
          </div>
        </el-card>

        <el-card shadow="never" class="table-card">
          <el-table :data="entries" v-loading="loading" size="small" empty-text="没有匹配的条目">
            <el-table-column label="条款" min-width="280">
              <template #default="{ row }">
                <div class="e-title" @click="openDetail(row)">
                  {{ row.regulation_name }}
                  <code v-if="row.clause_number">第{{ row.clause_number }}条</code>
                </div>
                <div class="e-sub">{{ row.title }}</div>
              </template>
            </el-table-column>
            <el-table-column label="规范号" width="130">
              <template #default="{ row }">
                <code class="e-code">{{ row.regulation_code || "-" }}</code>
              </template>
            </el-table-column>
            <el-table-column label="类别" width="130">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :style="{ color: CATEGORY_COLOR[row.category] }">
                  {{ CATEGORY_LABEL[row.category] || row.category }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="强制性" width="86">
              <template #default="{ row }">
                <el-tag v-if="row.is_mandatory" type="danger" size="small">强制</el-tag>
                <span v-else class="dim">-</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag
                  :type="row.is_active ? 'success' : 'info'"
                  size="small"
                  effect="plain"
                >
                  {{ row.is_active ? "有效" : "废止" }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="130" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openDetail(row)">
                  详情
                </el-button>
                <el-button link type="danger" size="small" @click="removeEntry(row)">
                  删除
                </el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="pager" v-if="entryTotal > 20">
            <el-pagination
              v-model:current-page="entryPage"
              :page-size="20"
              :total="entryTotal"
              layout="prev, pager, next, total"
              size="small"
              @current-change="loadEntries"
            />
          </div>
        </el-card>
      </el-tab-pane>

      <!-- ============ 构建 ============ -->
      <el-tab-pane label="构建与评测" name="build">
        <el-row :gutter="12">
          <el-col :span="10">
            <el-card shadow="never" class="panel">
              <template #header><span>索引维护</span></template>

              <div class="action">
                <div class="a-head">
                  <span class="a-title">重建向量索引</span>
                  <el-tag size="small" type="warning" effect="plain">换 Embedding 模型后必做</el-tag>
                </div>
                <p class="a-desc">
                  把所有法规条目重新向量化并写入 PGVector。知识库为空或覆盖率为 0 时用它。
                </p>
                <el-button
                  type="primary"
                  size="small"
                  :loading="rebuilding"
                  @click="doReindex"
                >
                  <el-icon><Refresh /></el-icon>开始重建
                </el-button>
              </div>

              <el-divider />

              <div class="action">
                <div class="a-head">
                  <span class="a-title">重载内存索引</span>
                  <el-tag size="small" type="info" effect="plain">增删条目后用</el-tag>
                </div>
                <p class="a-desc">
                  重新加载 BM25 索引。删除或新增法规条目后必须执行，否则检索不到新内容。
                </p>
                <el-button size="small" :loading="reloading" @click="doReload">
                  <el-icon><RefreshRight /></el-icon>立即重载
                </el-button>
              </div>

              <el-divider />

              <div class="action">
                <div class="a-head">
                  <span class="a-title">运行检索评测</span>
                  <el-tag size="small" type="success" effect="plain">JD 评测体系</el-tag>
                </div>
                <p class="a-desc">
                  用 27 条评测集跑检索质量，输出 Recall@5 / 命中率 / 拒答准确率。
                  改动检索逻辑后必须跑一次，确认没有回退。
                </p>
                <el-button size="small" :loading="runningEval" @click="doEval">
                  <el-icon><DataAnalysis /></el-icon>运行评测
                </el-button>
              </div>
            </el-card>
          </el-col>

          <el-col :span="14">
            <el-card shadow="never" class="panel">
              <template #header>
                <div class="panel-head">
                  <span>执行日志</span>
                  <el-button link size="small" @click="buildLog = []">清空</el-button>
                </div>
              </template>
              <div v-if="buildLog.length" class="log">
                <div v-for="(l, i) in buildLog" :key="i" class="log-row" :class="l.type">
                  <span class="l-time">{{ l.time }}</span>
                  <span class="l-text">{{ l.text }}</span>
                </div>
              </div>
              <div v-else class="empty-tip">
                还没有执行记录。<br />点击左侧按钮开始维护索引。
              </div>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>
    </el-tabs>

    <!-- 条目详情抽屉 -->
    <el-drawer v-model="drawerVisible" size="560px" :title="currentEntry?.title || '条目详情'">
      <div v-if="currentEntry" class="detail">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="规范名称">
            {{ currentEntry.regulation_name }}
          </el-descriptions-item>
          <el-descriptions-item label="规范号">
            <code>{{ currentEntry.regulation_code || "-" }}</code>
          </el-descriptions-item>
          <el-descriptions-item label="条款号">
            {{ currentEntry.clause_number || "-" }}
          </el-descriptions-item>
          <el-descriptions-item label="条目类型">
            {{ currentEntry.entry_type || "-" }}
          </el-descriptions-item>
          <el-descriptions-item label="危大类别">
            {{ CATEGORY_LABEL[currentEntry.category] || currentEntry.category }}
          </el-descriptions-item>
          <el-descriptions-item label="是否强制性条文">
            <el-tag v-if="currentEntry.is_mandatory" type="danger" size="small">强制性条文</el-tag>
            <span v-else class="dim">否</span>
          </el-descriptions-item>
          <el-descriptions-item label="时效">
            <el-tag
              :type="currentEntry.is_active ? 'success' : 'danger'"
              size="small"
              effect="plain"
            >
              {{ currentEntry.is_active ? "现行有效" : "已废止" }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <div class="d-sec-title">条文正文</div>
        <pre v-if="entryContent" class="d-content">{{ entryContent }}</pre>
        <div v-else class="empty-tip">
          该条目暂无正文缓存。<br />
          可在「法规速查」页按规范号+条款号查看完整条文。
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped lang="scss">
.kb-page {
  padding: 12px;
}

.kb-tabs :deep(.el-tabs__header) {
  margin-bottom: 12px;
}

.stat-row {
  margin-bottom: 12px;
}

.stat-box {
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 13px 14px;
  text-align: center;

  .s-value {
    font-size: 23px;
    font-weight: 500;
    color: var(--el-color-primary);
    font-variant-numeric: tabular-nums;
    line-height: 1.25;

    &.warn {
      color: var(--el-color-warning);
    }
  }

  .s-label {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    margin-top: 2px;
  }
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

.mt-10 {
  margin-top: 10px;
}

/* 健康度 */
.health {
  .h-row {
    display: flex;
    justify-content: space-between;
    padding: 7px 0;
    border-bottom: 1px dashed var(--el-border-color-lighter);
    font-size: 13px;

    &:last-of-type {
      border-bottom: none;
    }
  }

  .h-label {
    color: var(--el-text-color-secondary);
  }

  .h-value {
    font-weight: 500;
    font-variant-numeric: tabular-nums;

    &.small {
      font-size: 11.5px;
      font-weight: 400;
      font-family: Consolas, monospace;
    }
  }
}

.env-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 7px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);
  font-size: 12.5px;

  &:last-child {
    border-bottom: none;
  }
}

.env-label {
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
  margin-right: 12px;
}

.env-val {
  text-align: right;
  color: var(--el-text-color-regular);
}

/* 类别分布 */
.dist-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
}

.d-name {
  width: 138px;
  font-size: 12.5px;
  color: var(--el-text-color-regular);
  flex-shrink: 0;
}

.d-bar-wrap {
  flex: 1;
  height: 16px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  overflow: hidden;
}

.d-bar {
  height: 100%;
  border-radius: 4px;
  transition: width 0.3s;
  min-width: 2px;
}

.d-count {
  width: 34px;
  text-align: right;
  font-size: 12.5px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-primary);
}

/* 条目表 */
.toolbar {
  margin-bottom: 10px;

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

.e-title {
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

  code {
    font-size: 11.5px;
    color: var(--el-color-primary);
    background: var(--el-color-primary-light-9);
    padding: 0 5px;
    border-radius: 3px;
    margin-left: 5px;
    font-weight: 400;
  }
}

.e-sub {
  font-size: 11.5px;
  color: var(--el-text-color-placeholder);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.e-code {
  font-family: Consolas, monospace;
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
}

.dim {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  padding: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

/* 构建动作 */
.action {
  .a-head {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 5px;
  }

  .a-title {
    font-size: 13.5px;
    font-weight: 500;
  }

  .a-desc {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    line-height: 1.7;
    margin: 0 0 9px;
  }
}

/* 日志 */
.log {
  max-height: 380px;
  overflow-y: auto;
}

.log-row {
  display: flex;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);
  font-size: 12.5px;
  font-family: Consolas, monospace;

  .l-time {
    color: var(--el-text-color-placeholder);
    flex-shrink: 0;
  }

  .l-text {
    color: var(--el-text-color-regular);
    line-height: 1.6;
  }

  &.warn .l-text {
    color: var(--el-color-warning);
  }
  &.err .l-text {
    color: var(--el-color-danger);
  }
}

/* 抽屉 */
.detail :deep(.el-descriptions__label) {
  width: 108px;
  color: var(--el-text-color-secondary);
}

.d-sec-title {
  font-size: 13px;
  font-weight: 500;
  margin: 16px 0 8px;
}

.d-content {
  margin: 0;
  padding: 12px;
  background: var(--el-fill-color-lighter);
  border-radius: 6px;
  font-family: "Microsoft YaHei", Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
}

.empty-tip {
  font-size: 12.5px;
  color: var(--el-text-color-placeholder);
  line-height: 1.9;
  text-align: center;
  padding: 24px 0;
}
</style>