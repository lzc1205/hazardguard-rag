<script setup lang="ts">
/**
 * 报告预览（需求文档 7.4.10）。
 *
 * Word/PDF 导出做好了但没有浏览入口，等于白做——
 * 用户不会为了看结论去下载再打开 Word。
 *
 * 设计上刻意做成「报告纸张」的形态：单栏、窄栏距、
 * 结论与依据并排。这样在浏览器里读起来就像在读正式报告，
 * 演示时可以直接投屏给人看。
 */
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { reviewApi } from "@/api";
import { CONCLUSION_META } from "@/utils/constants";

const route = useRoute();
const router = useRouter();
const loading = ref(false);
const data = ref<any>(null);

const STATUS_LABEL: Record<string, string> = {
  compliant: "符合",
  non_compliant: "不符合",
  needs_confirmation: "待人工确认",
  not_applicable: "不适用",
};
const SEVERITY_LABEL: Record<string, string> = {
  critical: "严重",
  major: "重要",
  minor: "一般",
  suggestion: "建议",
};

const items = computed(() => data.value?.review_items || []);
const refusedCount = computed(() => items.value.filter((i: any) => i.refused).length);
const nonCompliantCount = computed(
  () => items.value.filter((i: any) => i.compliance_status === "non_compliant").length,
);
const citedCount = computed(
  () => items.value.filter((i: any) => i.citation_verified).length,
);

function fmtTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

/** 单条意见的状态配色 */
function statusType(s: string): string {
  return (
    { compliant: "success", non_compliant: "danger", needs_confirmation: "warning" }[
      s
    ] || "info"
  );
}

async function load() {
  loading.value = true;
  try {
    const id = route.params.id as string;
    const res = await reviewApi.reportPreview(id);
    data.value = res.data;
  } catch {
    ElMessage.error("加载报告失败");
  } finally {
    loading.value = false;
  }
}

/** 导出：走后端二进制接口，前端只负责触发下载 */
async function download(kind: "docx" | "pdf") {
  const id = route.params.id as string;
  const url = kind === "pdf" ? `/api/v1/reviews/${id}/report.pdf` : `/api/v1/reviews/${id}/report.docx`;
  const token = localStorage.getItem("token");
  try {
    const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    if (!resp.ok) throw new Error("导出失败");
    const blob = await resp.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${data.value?.title || "合规审查报告"}.${kind}`;
    a.click();
    URL.revokeObjectURL(a.href);
    ElMessage.success(`${kind.toUpperCase()} 已下载`);
  } catch {
    ElMessage.error("导出失败，请重试");
  }
}

onMounted(load);
</script>

<template>
  <div class="report-page" v-loading="loading">
    <div class="toolbar">
      <el-button size="small" @click="router.back()">
        <el-icon><ArrowLeft /></el-icon>返回
      </el-button>
      <span class="tb-title">审查报告</span>
      <div class="spacer" />
      <el-button size="small" @click="download('docx')">
        <el-icon><Document /></el-icon>下载 Word
      </el-button>
      <el-button type="primary" size="small" @click="download('pdf')">
        <el-icon><Download /></el-icon>下载 PDF
      </el-button>
    </div>

    <div v-if="data" class="paper">
      <!-- 报告抬头 -->
      <header class="rp-head">
        <h1>{{ data.title }}</h1>
        <div class="rp-meta">
          <span v-if="data.report_number">报告编号：{{ data.report_number }}</span>
          <span>生成时间：{{ fmtTime(new Date().toISOString()) }}</span>
        </div>
      </header>

      <!-- 一、审查结论 -->
      <section class="rp-section">
        <h2>一、审查结论</h2>
        <div class="kv-grid">
          <div class="kv"><span class="k">综合结论</span><span class="v">
            <el-tag :type="CONCLUSION_META[data.overall_conclusion || '']?.type || 'info'" size="small">
              {{ CONCLUSION_META[data.overall_conclusion || ""]?.label || "待定" }}
            </el-tag>
          </span></div>
          <div class="kv"><span class="k">合规评分</span><span class="v big">{{ data.overall_score ?? "-" }} 分</span></div>
          <div class="kv"><span class="k">是否危大工程</span><span class="v">{{ data.is_hazard_project ? "是" : "否" }}</span></div>
          <div class="kv"><span class="k">是否超规模</span><span class="v">
            <span :class="{ danger: data.is_over_scale }">{{ data.is_over_scale ? "是（须专家论证）" : "否" }}</span>
          </span></div>
          <div class="kv"><span class="k">危大类别</span><span class="v">{{ (data.hazard_categories || []).join("、") || "—" }}</span></div>
          <div class="kv"><span class="k">审查意见</span><span class="v">{{ items.length }} 条</span></div>
          <div class="kv"><span class="k">有法规依据</span><span class="v ok">{{ citedCount }} 条</span></div>
          <div class="kv"><span class="k">不符合项</span><span class="v" :class="{ danger: nonCompliantCount > 0 }">{{ nonCompliantCount }} 条</span></div>
        </div>

        <!-- 拒答披露：合规审查的关键信息，不能隐去 -->
        <el-alert
          v-if="refusedCount > 0"
          type="warning"
          :closable="false"
          class="refuse-alert"
          show-icon
        >
          <template #title>
            本报告有 {{ refusedCount }} 项因知识库未检索到现行有效的直接条款依据，
            系统按「无依据不出具判定」原则拒绝出具结论（详见二、审查意见明细）。
            该部分需由审查人员人工复核确认后方可作为审批依据。
          </template>
        </el-alert>
      </section>

      <!-- 二、审查意见明细 -->
      <section class="rp-section">
        <h2>二、审查意见明细</h2>
        <div v-if="!items.length" class="empty">本次审查未产生审查意见。</div>

        <article
          v-for="(it, i) in items"
          :key="i"
          class="item"
          :class="{ 'item-refused': it.refused, 'item-bad': it.compliance_status === 'non_compliant' }"
        >
          <div class="it-head">
            <span class="it-idx">{{ i + 1 }}</span>
            <span class="it-title">{{ it.item_title }}</span>
            <el-tag :type="statusType(it.compliance_status)" size="small" effect="plain">
              {{ STATUS_LABEL[it.compliance_status] || it.compliance_status }}
            </el-tag>
            <el-tag
              v-if="it.severity && it.compliance_status !== 'compliant'"
              size="small"
              :type="it.severity === 'critical' ? 'danger' : 'warning'"
              effect="plain"
            >
              {{ SEVERITY_LABEL[it.severity] || it.severity }}
            </el-tag>
          </div>

          <div class="it-body">
            <div class="it-row">
              <span class="it-label">法规依据</span>
              <template v-if="it.regulation_reference">
                <span class="it-ref">{{ it.regulation_reference }}</span>
                <el-tag
                  size="small"
                  :type="it.citation_verified ? 'success' : 'danger'"
                  effect="plain"
                  class="cite-tag"
                >
                  {{ it.citation_verified ? "已溯源" : "引用未通过校验" }}
                </el-tag>
              </template>
              <span v-else class="it-none">
                {{ it.refuse_reason || "知识库中无对应条款依据" }}
                <el-tag size="small" type="info" effect="plain" class="cite-tag">系统拒答</el-tag>
              </span>
            </div>

            <div v-if="it.review_comment" class="it-row">
              <span class="it-label">审查意见</span>
              <span class="it-text">{{ it.review_comment }}</span>
            </div>

            <div
              v-if="it.modification_suggestion && it.modification_suggestion !== '无需修改'"
              class="it-row"
            >
              <span class="it-label">修改建议</span>
              <span class="it-sugg">{{ it.modification_suggestion }}</span>
            </div>
          </div>
        </article>
      </section>

      <!-- 三、专家论证要点 -->
      <section v-if="data.expert_points?.length" class="rp-section">
        <h2>三、专家论证要点</h2>
        <el-alert type="info" :closable="false" class="mb-10">
          本工程超过一定规模，需组织专家论证。以下要点供专家组参考。
        </el-alert>
        <table class="rp-table">
          <thead>
            <tr><th style="width: 34px">#</th><th style="width: 140px">论证要点</th><th>内容</th></tr>
          </thead>
          <tbody>
            <tr v-for="(p, i) in data.expert_points" :key="i">
              <td>{{ i + 1 }}</td>
              <td class="pt-title">{{ p.point_title }}</td>
              <td>{{ p.point_content }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- 四、审查执行过程 -->
      <section v-if="data.agent_trace?.length" class="rp-section">
        <h2>四、审查执行过程</h2>
        <p class="sec-note">记录每个 Agent 的执行情况，便于复核结论的生成过程。</p>
        <table class="rp-table">
          <thead>
            <tr>
              <th>执行环节</th><th style="width: 80px">耗时</th>
              <th style="width: 90px">Token</th><th style="width: 90px">状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in data.agent_trace || []" :key="t.agent">
              <td>{{ t.agent }}</td>
              <td>{{ t.duration_ms }}ms</td>
              <td>{{ t.tokens }}</td>
              <td>
                <el-tag
                  size="small"
                  :type="t.degraded || t.error ? 'warning' : 'success'"
                  effect="plain"
                >
                  {{ t.degraded ? "降级" : t.error ? "异常" : "正常" }}
                </el-tag>
              </td>
            </tr>
          </tbody>
        </table>
        <p class="sec-note mt-8">
          累计 Token：{{ data.total_tokens ?? 0 }}　|　成本估算：¥{{ (data.cost_estimate ?? 0).toFixed(4) }}
        </p>
      </section>

      <!-- 使用说明 -->
      <section class="rp-section">
        <h2>使用说明</h2>
        <ol class="disclaimer">
          <li>本报告由系统依据现行法规库自动生成，供审查人员参考。</li>
          <li>报告结论仅对送审文本负责，方案修改后应重新审查。</li>
          <li>标记为「系统拒答」的条目表示知识库中无现行有效条款依据，须由具备相应资格的人员人工判定，不得直接作为审批依据。</li>
          <li>本报告不替代法定审查程序，不作为安全生产责任认定依据。</li>
        </ol>
      </section>
    </div>
  </div>
</template>

<style scoped lang="scss">
.report-page {
  padding: 12px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.tb-title {
  font-size: 15px;
  font-weight: 500;
}

.spacer {
  flex: 1;
}

/* 报告纸张：窄栏 + 居中，模拟正式文件 */
.paper {
  max-width: 900px;
  margin: 0 auto;
  background: #fff;
  padding: 40px 52px 48px;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
}

.rp-head {
  text-align: center;
  padding-bottom: 20px;
  margin-bottom: 24px;
  border-bottom: 2px solid var(--el-color-primary);

  h1 {
    margin: 0 0 8px;
    font-size: 22px;
    font-weight: 500;
    color: var(--el-text-color-primary);
    letter-spacing: 1px;
  }

  .rp-meta {
    display: flex;
    justify-content: center;
    gap: 20px;
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }
}

.rp-section {
  margin-bottom: 30px;

  h2 {
    font-size: 15px;
    font-weight: 500;
    color: var(--el-color-primary);
    margin: 0 0 14px;
    padding-left: 10px;
    border-left: 3px solid var(--el-color-primary);
    line-height: 1.4;
  }
}

.kv-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1px;
  background: var(--el-border-color-lighter);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  overflow: hidden;
}

.kv {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  padding: 9px 14px;
  font-size: 13px;

  .k {
    color: var(--el-text-color-secondary);
    flex-shrink: 0;
  }

  .v {
    font-weight: 500;
    text-align: right;
    margin-left: 12px;

    &.big {
      font-size: 15px;
      color: var(--el-color-primary);
    }
    &.ok {
      color: var(--el-color-success);
    }
    &.danger {
      color: var(--el-color-danger);
    }
  }
}

.refuse-alert {
  margin-top: 14px;

  :deep(.el-alert__title) {
    font-size: 12.5px;
    line-height: 1.75;
  }
}

/* 审查意见条目 */
.item {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 7px;
  margin-bottom: 12px;
  overflow: hidden;

  &.item-bad {
    border-left: 3px solid var(--el-color-danger);
  }

  &.item-refused {
    border-left: 3px solid var(--el-color-warning);
    background: #fffdf7;
  }
}

.it-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 13px;
  background: var(--el-fill-color-lighter);
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.it-idx {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--el-color-primary);
  color: #fff;
  font-size: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.it-title {
  flex: 1;
  font-size: 13.5px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  min-width: 0;
}

.it-body {
  padding: 11px 13px;
}

.it-row {
  display: flex;
  gap: 10px;
  margin-bottom: 7px;
  font-size: 12.5px;
  line-height: 1.7;

  &:last-child {
    margin-bottom: 0;
  }
}

.it-label {
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
  width: 58px;
}

.it-ref {
  color: var(--el-color-primary);
  line-height: 1.7;
}

.it-text {
  color: var(--el-text-color-regular);
}

.it-sugg {
  color: var(--el-color-success);
}

.it-none {
  color: var(--el-text-color-placeholder);
  font-style: italic;
}

.cite-tag {
  margin-left: 6px;
}

.rp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;

  th {
    background: var(--el-fill-color-light);
    padding: 8px 10px;
    text-align: left;
    font-weight: 500;
    color: var(--el-text-color-regular);
    border: 1px solid var(--el-border-color-lighter);
  }

  td {
    padding: 8px 10px;
    border: 1px solid var(--el-border-color-lighter);
    line-height: 1.7;
    vertical-align: top;
    color: var(--el-text-color-regular);
  }

  .pt-title {
    font-weight: 500;
    color: var(--el-text-color-primary);
  }
}

.sec-note {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin: 0 0 10px;
}

.disclaimer {
  margin: 0;
  padding-left: 20px;
  font-size: 12.5px;
  line-height: 2;
  color: var(--el-text-color-regular);
}

.mb-10 {
  margin-bottom: 10px;
}

.mt-8 {
  margin-top: 8px;
}

.empty {
  font-size: 13px;
  color: var(--el-text-color-placeholder);
  padding: 20px 0;
  text-align: center;
}
</style>