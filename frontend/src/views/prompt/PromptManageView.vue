<script setup lang="ts">
/**
 * Prompt 管理页。
 *
 * 对应 JD「Prompt Engineering 47%」的工程化展示面。
 *
 * 设计取向：**让使用者能读到真实的 Prompt 内容**。
 * 只显示模板名和字符数等于没展示——Prompt 工程的价值在于
 * 「硬约束怎么写的」「少样本怎么给的」，这些必须能直接看到。
 */
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { promptApi, type PromptInfo, type PromptVersion } from "@/api";

const prompts = ref<PromptInfo[]>([]);
const versions = ref<PromptVersion[]>([]);
const llmStatus = ref<Record<string, any> | null>(null);
const selected = ref<PromptInfo | null>(null);
const loading = ref(false);
const tab = ref<"content" | "versions">("content");

/** 各模板的定位说明——让管理者知道每个模板解决什么问题 */
const PURPOSE: Record<string, string> = {
  compliance_review: "审查说理核心：把「条款 + 方案表述」转成可复核的审查意见",
  regulatory_qa: "问答环节：带条款号作答，无依据时拒答",
  expert_argumentation: "超规模工程才触发：生成专家组质询清单",
  report_summary: "报告导出：把结构化意见汇成客观摘要",
  remediation: "整改环节：把不符合项转成可下发的整改要求",
  query_rewrite: "检索优化：口语化提问转检索式",
  document_extract: "文档解析：抽取危大判定参数",
  hazard_explain: "判定说明：把阈值比对写成人可读说明",
};

/** 真实调用方，用于说明哪些模板已在链路上 */
const USED_BY: Record<string, string> = {
  compliance_review: "ComplianceReviewerAgent · LangChain 审查链",
  regulatory_qa: "智能问答 direct 模式 · 问答链",
  expert_argumentation: "ExpertArgumentationAgent",
  report_summary: "ReportGeneratorAgent",
  remediation: "整改建议环节（已定义，待接入）",
  query_rewrite: "检索前置优化（已定义，待接入）",
  document_extract: "DocumentParserAgent LLM 兜底路径（已定义，待接入）",
  hazard_explain: "危大判定展示层（已定义，待接入）",
};

async function load() {
  loading.value = true;
  try {
    const [p, v, l] = await Promise.all([
      promptApi.list(),
      promptApi.versions(),
      promptApi.llmStatus(),
    ]);
    prompts.value = p.data || [];
    versions.value = v.data.items || [];
    llmStatus.value = l.data;
    if (prompts.value.length) selected.value = prompts.value[0];
  } catch {
    ElMessage.error("加载 Prompt 列表失败");
  } finally {
    loading.value = false;
  }
}

async function doSync() {
  try {
    const res = await promptApi.sync(true);
    const d = res.data as any;
    ElMessage.success(`已同步：新增 ${d.created}，更新 ${d.updated}，跳过 ${d.skipped}`);
    load();
  } catch {
    ElMessage.error("同步失败");
  }
}

const versionsOfSelected = computed(() =>
  selected.value
    ? versions.value.filter((v) => v.name === selected.value!.name)
    : [],
);

/**
 * 把模板正文切成区块，区分「硬约束」并逐条编号。
 *
 * 直接渲染原始文本看不出重点——合规 Prompt 最值钱的是那几条
 * 「不许做什么」，必须单独框出来。
 */
interface Block {
  type: "text" | "constraints" | "example" | "io";
  title: string;
  lines: string[];
}

const blocks = computed<Block[]>(() => {
  const tpl = selected.value?.template || "";
  const out: Block[] = [];
  let cur: Block | null = null;

  const flush = () => {
    if (cur && cur.lines.length) out.push(cur);
    cur = null;
  };

  for (const raw of tpl.split("\n")) {
    const line = raw.trimEnd();
    if (line.startsWith("## ")) {
      flush();
      const title = line.replace(/^##\s*/, "");
      let type: Block["type"] = "text";
      if (title.includes("硬约束")) type = "constraints";
      else if (title.includes("示例")) type = "example";
      else if (title.includes("输出") || title.includes("输入") || title.includes("待") || title.includes("参数") || title.includes("问题") || title.includes("特征") || title.includes("资料") || title.includes("依据") || title.includes("内容") || title.includes("格式") || title.includes("清单") || title.includes("结果") || title.includes("参考")) type = "io";
      cur = { type, title, lines: [] };
      continue;
    }
    if (cur) cur.lines.push(line);
  }
  flush();
  return out;
});

/** 变量高亮：把 {variable} 标成可点击的 chip 样式 */
function highlightVars(text: string): Array<{ t: string; v: boolean }> {
  const out: Array<{ t: string; v: boolean }> = [];
  const re = /\{(\w+)\}/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push({ t: text.slice(last, m.index), v: false });
    out.push({ t: m[0], v: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ t: text.slice(last), v: false });
  return out;
}

async function copyTemplate() {
  const tpl = selected.value?.template;
  if (!tpl) return;
  try {
    await navigator.clipboard.writeText(tpl);
    ElMessage.success("模板正文已复制到剪贴板");
  } catch {
    ElMessage.warning("复制失败，请手动选中复制");
  }
}

function fmtTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

onMounted(load);
</script>

<template>
  <div class="prompt-page" v-loading="loading">
    <!-- 顶部状态条 -->
    <el-card shadow="never" class="status-card">
      <div class="status-row">
        <div class="status-item">
          <span class="si-label">LLM 模式</span>
          <el-tag v-if="llmStatus" :type="llmStatus.available ? 'success' : 'info'" size="small">
            {{ llmStatus.available ? llmStatus.model : "规则模式（未配置 Key）" }}
          </el-tag>
        </div>
        <div class="status-item">
          <span class="si-label">模板总数</span>
          <span class="si-value">{{ prompts.length }}</span>
        </div>
        <div class="status-item">
          <span class="si-label">版本记录</span>
          <span class="si-value">{{ versions.length }}</span>
        </div>
        <div class="status-item">
          <span class="si-label">硬约束总数</span>
          <span class="si-value">
            {{ prompts.reduce((s, p) => s + (p.hard_constraints || 0), 0) }} 条
          </span>
        </div>
        <div class="spacer" />
        <el-button size="small" @click="doSync">同步到数据库</el-button>
      </div>
      <div v-if="llmStatus" class="status-note">{{ llmStatus.note }}</div>
    </el-card>

    <el-row :gutter="12">
      <!-- 模板列表 -->
      <el-col :span="7">
        <el-card shadow="never" class="panel list-panel">
          <template #header>
            <div class="panel-head">
              <span class="panel-title">Prompt 模板</span>
              <span class="panel-sub">{{ prompts.length }} 个</span>
            </div>
          </template>
          <div class="prompt-list">
            <div
              v-for="p in prompts"
              :key="p.name"
              class="prompt-item"
              :class="{ active: selected?.name === p.name }"
              @click="selected = p"
            >
              <div class="pi-top">
                <span class="pi-name">{{ PURPOSE[p.name] || p.name }}</span>
                <el-tag size="small" effect="plain" class="pi-ver">v{{ p.version }}</el-tag>
              </div>
              <div class="pi-desc">{{ p.changelog }}</div>
              <div class="pi-meta">
                <span><el-icon><Lock /></el-icon> 约束 {{ p.hard_constraints }}</span>
                <span><el-icon><Document /></el-icon> {{ p.length }} 字</span>
                <span v-if="p.few_shot" class="pi-shot">含少样本</span>
              </div>
            </div>
          </div>
        </el-card>
      </el-col>

      <!-- 模板详情 -->
      <el-col :span="17">
        <el-card shadow="never" class="panel detail-panel">
          <template #header>
            <div class="detail-head">
              <div class="dh-left">
                <span class="panel-title">{{ PURPOSE[selected?.name || ''] || '模板详情' }}</span>
                <code class="dh-code">{{ selected?.name }}</code>
                <el-tag size="small" type="success" effect="plain">v{{ selected?.version }}</el-tag>
              </div>
              <div class="dh-right">
                <el-radio-group v-model="tab" size="small">
                  <el-radio-button value="content">模板正文</el-radio-button>
                  <el-radio-button value="versions">
                    版本记录 {{ versionsOfSelected.length }}
                  </el-radio-button>
                </el-radio-group>
                <el-button size="small" :disabled="!selected" @click="copyTemplate">
                  <el-icon><CopyDocument /></el-icon>复制正文
                </el-button>
              </div>
            </div>
          </template>

          <!-- 正文 -->
          <template v-if="tab === 'content' && selected">
            <el-alert type="info" :closable="false" class="purpose-alert">
              <template #title>
                <span class="pa-title">{{ selected.changelog }}</span>
              </template>
              <div class="pa-meta">
                <span><b>调用方</b>：{{ USED_BY[selected.name] || "—" }}</span>
                <span><b>变量</b>：{{ selected.variables.join("、") || "无" }}</span>
                <span><b>硬约束</b> {{ selected.hard_constraints }} 条</span>
                <span><b>少样本</b> {{ selected.few_shot ? "有" : "无" }}</span>
              </div>
            </el-alert>

            <div class="prompt-body">
              <div v-for="(b, i) in blocks" :key="i" class="block" :class="`block-${b.type}`">
                <div class="block-head">
                  <el-icon v-if="b.type === 'constraints'"><WarnTriangleFilled /></el-icon>
                  <el-icon v-else-if="b.type === 'example'"><Reading /></el-icon>
                  <span>{{ b.title }}</span>
                  <span v-if="b.type === 'constraints'" class="block-badge">
                    {{ b.lines.filter((l) => l.trim() && /^\d/.test(l.trim())).length }} 条不可违反
                  </span>
                </div>
                <div class="block-body">
                  <p
                    v-for="(line, j) in b.lines"
                    :key="j"
                    class="b-line"
                    :class="{ 'is-constraint': b.type === 'constraints' && /^\d/.test(line.trim()) && line.trim() }"
                  >
                    <template v-for="(seg, k) in highlightVars(line)" :key="k">
                      <mark v-if="seg.v" class="var">{{ seg.t }}</mark>
                      <template v-else>{{ seg.t }}</template>
                    </template>
                  </p>
                </div>
              </div>
            </div>
          </template>

          <!-- 版本记录 -->
          <template v-else>
            <el-table
              :data="versionsOfSelected"
              size="small"
              empty-text="暂无版本记录"
              class="version-table"
            >
              <el-table-column label="版本" width="90">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.is_active ? 'success' : 'info'">
                    v{{ row.version }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="改动说明" min-width="260">
                <template #default="{ row }">
                  <div class="vc-log">{{ row.changelog }}</div>
                </template>
              </el-table-column>
              <el-table-column label="评测分数" width="150">
                <template #default="{ row }">
                  <span v-if="row.eval_score" class="score">
                    {{ Object.entries(row.eval_score)
                      .filter(([k]) => k !== "history")
                      .map(([k, v]) => `${k}=${v}`)
                      .join(", ") }}
                  </span>
                  <span v-else class="dim">未评测</span>
                </template>
              </el-table-column>
              <el-table-column label="创建时间" width="160">
                <template #default="{ row }">
                  <span class="dim">{{ fmtTime(row.created_at) }}</span>
                </template>
              </el-table-column>
            </el-table>
          </template>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped lang="scss">
.prompt-page {
  padding: 12px;
}

.status-card {
  margin-bottom: 12px;

  :deep(.el-card__body) {
    padding: 12px 16px;
  }
}

.status-row {
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
}

.status-item {
  display: flex;
  align-items: center;
  gap: 7px;

  .si-label {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  .si-value {
    font-size: 14px;
    font-weight: 600;
    color: var(--el-color-primary);
    font-variant-numeric: tabular-nums;
  }
}

.spacer {
  flex: 1;
}

.status-note {
  margin-top: 7px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}

.panel :deep(.el-card__body) {
  padding: 0;
}

.list-panel :deep(.el-card__body) {
  padding: 8px;
  max-height: calc(100vh - 220px);
  overflow-y: auto;
}

.detail-panel :deep(.el-card__body) {
  padding: 14px;
  max-height: calc(100vh - 220px);
  overflow-y: auto;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.panel-title {
  font-size: 14px;
  font-weight: 600;
}

.panel-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.prompt-item {
  padding: 9px 10px;
  border-radius: 7px;
  cursor: pointer;
  margin-bottom: 5px;
  border: 1px solid transparent;
  transition: background 0.15s, border-color 0.15s;

  &:hover {
    background: var(--el-fill-color-light);
  }

  &.active {
    background: var(--el-color-primary-light-9);
    border-color: var(--el-color-primary-light-7);
  }

  .pi-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 3px;
  }

  .pi-name {
    font-size: 13px;
    font-weight: 600;
    color: var(--el-text-color-primary);
    line-height: 1.4;
  }

  .pi-ver {
    flex-shrink: 0;
  }

  .pi-desc {
    font-size: 11.5px;
    color: var(--el-text-color-secondary);
    line-height: 1.5;
    margin-bottom: 5px;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .pi-meta {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 11px;
    color: var(--el-text-color-placeholder);

    span {
      display: inline-flex;
      align-items: center;
      gap: 3px;
    }

    .pi-shot {
      color: var(--el-color-success);
      background: var(--el-color-success-light-9);
      padding: 0 5px;
      border-radius: 3px;
    }
  }
}

.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.dh-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.dh-code {
  font-family: Consolas, monospace;
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  padding: 1px 6px;
  border-radius: 4px;
}

.dh-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.purpose-alert {
  margin-bottom: 12px;

  .pa-title {
    font-size: 12.5px;
    font-weight: 500;
    line-height: 1.6;
  }

  .pa-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 18px;
    margin-top: 5px;
    font-size: 11.5px;
    color: var(--el-text-color-regular);

    b {
      color: var(--el-text-color-secondary);
      font-weight: 500;
    }
  }
}

.prompt-body {
  font-size: 13px;
  line-height: 1.75;
  color: var(--el-text-color-primary);
}

.block {
  margin-bottom: 14px;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--el-border-color-lighter);
}

.block-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  background: var(--el-fill-color-light);
  font-size: 12.5px;
  font-weight: 600;
  color: var(--el-text-color-regular);
  border-bottom: 1px solid var(--el-border-color-lighter);

  .el-icon {
    color: var(--el-text-color-secondary);
  }
}

.block-badge {
  margin-left: auto;
  font-size: 10.5px;
  font-weight: 500;
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
  padding: 1px 7px;
  border-radius: 9px;
}

/* 硬约束区块：这是 Prompt 最值钱的部分，视觉上必须突出 */
.block-constraints {
  border-color: var(--el-color-warning-light-5);

  .block-head {
    background: var(--el-color-warning-light-9);
    color: var(--el-color-warning-dark-2);

    .el-icon {
      color: var(--el-color-warning);
    }
  }

  .block-body {
    background: #fffdf7;
  }

  .is-constraint {
    padding-left: 10px;
    border-left: 2px solid var(--el-color-warning);
    margin: 6px 0;
    color: var(--el-text-color-primary);
  }
}

.block-example {
  .block-head {
    background: var(--el-color-success-light-9);
    color: var(--el-color-success-dark-2);

    .el-icon {
      color: var(--el-color-success);
    }
  }

  .block-body {
    background: #f9fdf9;
  }
}

.block-io .block-head {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary-dark-2);

  .el-icon {
    color: var(--el-color-primary);
  }
}

.block-body {
  padding: 10px 12px;
}

.b-line {
  margin: 0 0 3px;
  white-space: pre-wrap;
  word-break: break-word;

  &:last-child {
    margin-bottom: 0;
  }

  &:empty {
    height: 7px;
  }
}

.var {
  background: var(--el-color-primary-light-8);
  color: var(--el-color-primary-dark-2);
  padding: 0 4px;
  border-radius: 3px;
  font-family: Consolas, monospace;
  font-size: 12px;
  font-weight: 600;
}

.vc-log {
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
}

.score {
  font-size: 11.5px;
  color: var(--el-color-success);
  font-variant-numeric: tabular-nums;
}

.dim {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>