<script setup lang="ts">
/**
 * 审查工作台（核心页面）。
 *
 * 三栏布局：
 *   左栏  方案上传/输入 + 危大识别结论 + 审查意见列表
 *   中栏  方案正文/审查意见详情
 *   右栏  Agent 执行链 + 成本统计
 *
 * 设计意图：让「审查结论」「依据」「执行过程」三者同时可见，
 * 体现可溯源与可观测，而不是只给一个结论。
 */
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { ElMessage } from "element-plus";
import { documentApi, reviewApi, searchApi, taskApi } from "@/api";
import { usePolling } from "@/composables/usePolling";
import AgentTracePanel from "@/components/review/AgentTracePanel.vue";
import ReviewItemCard from "@/components/review/ReviewItemCard.vue";
import {
  categoryMeta,
  categoryName,
  CONCLUSION_META,
  type ReviewDetail,
} from "@/utils/constants";

const route = useRoute();

const SAMPLE_TEXT = `一、工程概况
本工程位于郑州市郑东新区，基坑开挖深度 6.5m，基坑周长约 320m，
周边有在用建筑与地下管线。地质条件为粉质黏土。

二、编制依据
《建筑基坑支护技术规程》JGJ 120-2012、《危险性较大的分部分项工程安全管理条例》

三、施工计划
分三层开挖，每层不超过 1.5m，随挖随支。

四、施工工艺技术
采用钻孔灌注桩 + 钢筋混凝土内支撑的支护形式。

五、施工安全保证措施
设置基坑周边防护栏，堆载不超过 20kN/m2。

六、施工管理及作业人员配备和分工
项目经理、技术负责人各 1 人，安全员 2 人。

七、验收要求
每层开挖完成后进行验收，验收合格后方可继续施工。

八、应急处置措施
制定应急预案，配备应急物资。

九、计算书及相关施工图纸
详见计算书与支护结构设计图。`;

// ---------------------------------------------------------------- 状态

const form = ref({ title: "", text: SAMPLE_TEXT, project_id: "", document_id: "" });
const submitting = ref(false);
/** 异步模式：走 Celery，后台审查 + 实时进度 */
const asyncMode = ref(false);
// 用可取消轮询器，组件卸载时自动停止，避免离开页面后仍在后台改写状态
const { progress: taskProgress, stage: taskStage, poll, stop: stopPolling } =
  usePolling();
const detail = ref<ReviewDetail | null>(null);
const activeItem = ref(0);
const filterStatus = ref<string>("");

const history = ref<Array<{ id: string; title: string; score: number | null }>>([]);
/** 当前审查 ID，报告下载需要 */
const currentId = ref<string>("");
const downloading = ref(false);

// ---------------------------------------------------------------- 计算属性

const items = computed(() => detail.value?.review_items || []);

const filteredItems = computed(() => {
  if (!filterStatus.value) return items.value;
  return items.value.filter((i) => i.compliance_status === filterStatus.value);
});

const stats = computed(() => {
  const list = items.value;
  const cited = list.filter((i) => i.citation_verified).length;
  const refused = list.filter((i) => i.refused).length;
  return {
    total: list.length,
    compliant: list.filter((i) => i.compliance_status === "compliant").length,
    nonCompliant: list.filter((i) => i.compliance_status === "non_compliant").length,
    needsConfirm: list.filter((i) => i.compliance_status === "needs_confirmation").length,
    cited,
    refused,
  };
});

const conclusionMeta = computed(() =>
  CONCLUSION_META[detail.value?.overall_conclusion || ""] || CONCLUSION_META.pass,
);

/** 方案正文按「一、二、三…」小节切分，供中栏目录使用。 */
const sections = computed(() => {
  const text = form.value.text;
  if (!text) return [];
  const lines = text.split("\n");
  const result: Array<{ title: string; content: string }> = [];
  let current: { title: string; lines: string[] } | null = null;

  for (const line of lines) {
    if (/^[一二三四五六七八九十]、/.test(line.trim())) {
      if (current) {
        result.push({ title: current.title, content: current.lines.join("\n") });
      }
      current = { title: line.trim(), lines: [] };
    } else if (current) {
      current.lines.push(line);
    }
  }
  if (current) {
    result.push({ title: current.title, content: current.lines.join("\n") });
  }
  return result;
});

// ---------------------------------------------------------------- 方法

async function submitReview() {
  if (!form.value.text.trim()) {
    ElMessage.warning("请先输入或上传专项施工方案内容");
    return;
  }
  // 连续提交两次时，作废上一轮轮询，避免两个任务的状态互相覆盖
  stopPolling();
  submitting.value = true;
  try {
    const res = await reviewApi.create({
      title: form.value.title || `方案审查 ${new Date().toLocaleString("zh-CN")}`,
      text: form.value.text,
      // 带上来源文档与项目，审查记录才能归集到对应项目/文档下。
      // route.query 的值可能是数组，统一取首项转字符串
      document_id:
        form.value.document_id ||
        (Array.isArray(route.query.document_id)
          ? String(route.query.document_id[0])
          : route.query.document_id
            ? String(route.query.document_id)
            : null) ||
        undefined,
      project_id:
        form.value.project_id ||
        (Array.isArray(route.query.project_id)
          ? String(route.query.project_id[0])
          : route.query.project_id
            ? String(route.query.project_id)
            : null) ||
        undefined,
      // 默认同步：简单方案 1~2 秒就能返回完整结果。
      // 大文档可切异步（走 Celery，进度条实时显示）
      async_mode: asyncMode.value,
    });

    if (asyncMode.value) {
      // 异步模式：只拿到 task_id，转轮询
      const taskId = (res.data as any).task_id;
      ElMessage.info("任务已提交，正在后台审查…");
      await pollTask(taskId);
      return;
    }

    detail.value = res.data as unknown as ReviewDetail;
    activeItem.value = 0;
    currentId.value = (res.data as any).review_id;
    history.value.unshift({
      id: (res.data as any).review_id,
      title: form.value.title,
      score: (res.data as any).overall_score,
    });
    const s = stats.value;
    ElMessage.success(
      `审查完成：${s.total} 条意见，其中 ${s.refused} 条因证据不足拒答`,
    );
  } catch (e: any) {
    // **不能吞掉真实原因**。原来统一弹「请检查后端服务」，
    // 但真实情况可能是超时、LLM 不可用、参数缺失——
    // 用户完全无法定位。request 拦截器已弹过一次，这里补充审查场景的说明。
    const msg = e?.message || "未知错误";
    const isTimeout = /timeout|超时/i.test(msg);
    ElMessage({
      message: isTimeout
        ? "审查超时（超过 120 秒）。方案可能过长，或 LLM 服务响应慢。可改用「异步模式」后台审查"
        : `审查失败：${msg}`,
      duration: 7000,
      showClose: true,
    });
  } finally {
    submitting.value = false;
  }
}

/** 轮询异步审查任务进度 */
async function pollTask(taskId: string) {
  const done = await poll<any>(
    async () => (await taskApi.status(taskId)).data,
    {
      interval: 1000,
      max: 180,
      onTick: (t) => {
        taskProgress.value = t.progress ?? 0;
        taskStage.value = t.current_stage || "处理中";
      },
      isDone: (t) => t.status === "completed" && !!t.result,
      onDone: async (t) => {
        await loadDetail(t.result.review_id);
        await loadHistory();
        ElMessage.success("异步审查完成");
      },
      onFail: (t) => {
        if (t.status === "failed") {
          ElMessage.error(`审查失败：${t.error_message || "未知原因"}`);
          return true;
        }
        return false;
      },
    },
  );

  if (!done && !taskStage.value) {
    ElMessage.warning("等待超时，可稍后在「历史审查」中查看结果");
  }
}

async function loadHistory() {
  try {
    const res = await reviewApi.list({ page: 1, page_size: 20 });
    history.value = res.data.items.map((r) => ({
      id: r.id,
      title: r.title,
      score: r.overall_score,
    }));
  } catch {
    history.value = [];
  }
}

async function loadDetail(id: string) {
  try {
    const res = await reviewApi.detail(id);
    detail.value = res.data;
    currentId.value = id;
    activeItem.value = 0;
  } catch {
    ElMessage.error("加载审查详情失败");
  }
}

/** 下载 Word 审查报告。 */
async function downloadReport() {
  const id = currentId.value || detail.value?.id;
  if (!id) {
    ElMessage.warning("请先发起一次审查");
    return;
  }
  downloading.value = true;
  try {
    const token = localStorage.getItem("token");
    const resp = await fetch(`/api/v1/reviews/${id}/report.docx`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const blob = await resp.blob();
    // 从 Content-Disposition 取文件名（后端用 RFC 5987 编码，需解码）
    const cd = resp.headers.get("Content-Disposition") || "";
    const match = cd.match(/filename\*=UTF-8''([^;]+)/);
    const filename = match ? decodeURIComponent(match[1]) : "合规审查报告.docx";

    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    ElMessage.success("报告已下载");
  } catch {
    ElMessage.error("报告下载失败，请确认后端服务正常");
  } finally {
    downloading.value = false;
  }
}

onMounted(async () => {
  await loadHistory();

  // 支持从文档页/项目页跳转过来：
  //   ?id=xxx        直接打开某次审查详情
  //   ?document_id=  预填标题，并自动拉取该文档正文（不必手动粘贴）
  //   ?project_id=   关联到项目
  const q = route.query;

  if (q.id) {
    await loadDetail(String(q.id));
    return;
  }

  if (q.title) {
    form.value.title = String(q.title);
  }
  if (q.project_id) {
    form.value.project_id = String(q.project_id);
  }
  if (q.document_id) {
    form.value.document_id = String(q.document_id);
    await loadDocumentIntoForm(String(q.document_id));
  }
});

/** 拉取文档正文并填入输入框 */
async function loadDocumentIntoForm(documentId: string) {
  try {
    const res = await documentApi.detail(documentId);
    const d = res.data;
    if (d.content_preview) {
      form.value.text = d.content_preview;
      if (!form.value.title) form.value.title = d.title;
      ElMessage.success(`已载入《${d.title}》解析后的正文`);
    } else {
      ElMessage.warning("该文档未解析出正文，请手动粘贴方案文本");
    }
  } catch {
    ElMessage.error("载入文档失败");
  }
}
</script>

<template>
  <div class="review-workbench">
    <!-- ==================== 左栏：输入与结论 ==================== -->
    <aside class="pane pane-left">
      <el-card shadow="never" class="input-card">
        <template #header><span class="pane-title">方案输入</span></template>

        <el-form label-position="top" size="default">
          <el-form-item label="审查任务名称">
            <el-input v-model="form.title" placeholder="如：XX 项目深基坑专项方案审查" clearable />
          </el-form-item>
          <el-form-item label="专项施工方案正文">
            <el-input
              v-model="form.text"
              type="textarea"
              :rows="8"
              placeholder="粘贴专项施工方案内容，或上传 PDF/Word 后自动解析"
            />
          </el-form-item>
          <el-form-item label="执行模式">
            <div class="mode-row">
              <el-radio-group v-model="asyncMode" size="small">
                <el-radio-button :value="false">同步（快速）</el-radio-button>
                <el-radio-button :value="true">异步（长文档）</el-radio-button>
              </el-radio-group>
              <span class="mode-tip">
                {{ asyncMode ? "后台执行，可实时看到每个 Agent 进度" : "阻塞等待 1~2 秒返回完整结果" }}
              </span>
            </div>
          </el-form-item>

          <el-form-item>
            <el-button
              type="primary"
              :loading="submitting"
              class="submit-btn"
              @click="submitReview"
            >
              <el-icon><VideoPlay /></el-icon>
              {{ submitting ? "多智能体审查中..." : "发起合规审查" }}
            </el-button>
          </el-form-item>

          <!-- 异步模式进度条：让用户知道系统没卡死 -->
          <el-form-item v-if="taskStage">
            <div class="task-progress">
              <div class="tp-head">
                <span class="tp-stage">{{ taskStage }}</span>
                <span class="tp-pct">{{ taskProgress }}%</span>
              </div>
              <el-progress
                :percentage="taskProgress"
                :stroke-width="6"
                :show-text="false"
                striped
                striped-flow
              />
            </div>
          </el-form-item>
        </el-form>

        <div class="hint">
          <el-icon><InfoFilled /></el-icon>
          审查由 6 个 Agent 协同完成，结论强制绑定法规依据，无依据将拒答
        </div>
      </el-card>

      <!-- 危大识别结论 -->
      <el-card v-if="detail" shadow="never" class="verdict-card">
        <template #header><span class="pane-title">识别结论</span></template>

        <div class="verdict-row">
          <el-tag :type="detail.is_hazard_project ? 'danger' : 'info'" effect="dark">
            {{ detail.is_hazard_project ? "属于危大工程" : "非危大工程" }}
          </el-tag>
          <el-tag v-if="detail.is_over_scale" type="warning" effect="dark">
            超过一定规模
          </el-tag>
        </div>

        <div v-if="detail.hazard_categories?.length" class="categories">
          <el-tag
            v-for="c in detail.hazard_categories"
            :key="c"
            size="small"
            effect="plain"
            :style="{ borderColor: categoryMeta(c).color, color: categoryMeta(c).color }"
          >
            {{ categoryMeta(c).icon }} {{ categoryName(c) }}
          </el-tag>
        </div>

        <!-- 命中阈值依据 -->
        <div v-if="detail.hazard_params?.length" class="thresholds">
          <div class="thresholds-title">阈值命中依据</div>
          <div v-for="(p, i) in detail.hazard_params" :key="i" class="threshold-item">
            <el-icon><Right /></el-icon>
            <span>{{ p.basis }}</span>
            <el-tag v-if="p.is_over_scale" size="small" type="warning" effect="plain">
              超规模
            </el-tag>
          </div>
        </div>

        <div class="score-block">
          <div class="score">
            <span class="score-num">{{ detail.overall_score ?? "-" }}</span>
            <span class="score-unit">分</span>
          </div>
          <el-tag :type="conclusionMeta.type" effect="dark" size="large">
            {{ conclusionMeta.label }}
          </el-tag>
        </div>
      </el-card>

      <!-- 历史记录 -->
      <el-card v-if="history.length" shadow="never" class="history-card">
        <template #header><span class="pane-title">历史审查</span></template>
        <div
          v-for="h in history"
          :key="h.id"
          class="history-item"
          :class="{ active: detail?.id === h.id }"
          @click="loadDetail(h.id)"
        >
          <span class="h-title">{{ h.title }}</span>
          <span class="h-score">{{ h.score ?? "-" }}分</span>
        </div>
      </el-card>
    </aside>

    <!-- ==================== 中栏：审查意见 ==================== -->
    <section class="pane pane-center">
      <div class="center-header">
        <h3>审查意见</h3>
        <div class="header-actions">
          <div v-if="items.length" class="filters">
            <el-radio-group v-model="filterStatus" size="small">
              <el-radio-button value="">全部 {{ stats.total }}</el-radio-button>
              <el-radio-button value="compliant">符合 {{ stats.compliant }}</el-radio-button>
              <el-radio-button value="non_compliant">
                不符合 {{ stats.nonCompliant }}
              </el-radio-button>
              <el-radio-button value="needs_confirmation">
                待确认 {{ stats.needsConfirm }}
              </el-radio-button>
            </el-radio-group>
          </div>
          <el-button
            v-if="items.length"
            type="primary"
            size="small"
            :loading="downloading"
            plain
            @click="downloadReport"
          >
            <el-icon><Download /></el-icon>
            导出报告
          </el-button>
        </div>
      </div>

      <!-- 统计条 -->
      <div v-if="items.length" class="stat-bar">
        <div class="stat">
          <span class="num">{{ stats.total }}</span><span class="label">总意见</span>
        </div>
        <div class="stat ok">
          <span class="num">{{ stats.compliant }}</span><span class="label">符合</span>
        </div>
        <div class="stat danger">
          <span class="num">{{ stats.nonCompliant }}</span><span class="label">不符合</span>
        </div>
        <div class="stat warn">
          <span class="num">{{ stats.needsConfirm }}</span><span class="label">待确认</span>
        </div>
        <div class="stat cite">
          <span class="num">{{ stats.cited }}</span><span class="label">有依据</span>
        </div>
        <div class="stat refuse">
          <span class="num">{{ stats.refused }}</span><span class="label">拒答</span>
        </div>
      </div>

      <!-- 拒答说明横幅 -->
      <el-alert
        v-if="stats.refused > 0"
        type="warning"
        :closable="false"
        show-icon
        class="refuse-alert"
      >
        <template #title>
          {{ stats.refused }} 条意见因证据不足被拒答
        </template>
        系统坚持「无可靠依据不出具判定」原则，这部分需人工复核确认。
      </el-alert>

      <!-- 意见列表 -->
      <div class="items-scroll">
        <ReviewItemCard
          v-for="(item, idx) in filteredItems"
          :key="idx"
          :item="item"
          :index="idx"
          @click="activeItem = idx"
          :class="{ 'is-active': activeItem === idx }"
        />
        <el-empty v-if="!items.length && !submitting" description="尚未发起审查" />
      </div>
    </section>

    <!-- ==================== 右栏：执行过程 ==================== -->
    <aside class="pane pane-right">
      <el-card shadow="never" class="trace-card">
        <AgentTracePanel :trace="detail?.agent_trace || null" :active="submitting" />
      </el-card>

      <!-- 成本统计：JD 成本优化 10% -->
      <el-card v-if="detail?.token_summary" shadow="never" class="cost-card">
        <template #header><span class="pane-title">Token 与成本</span></template>

        <div class="cost-summary">
          <div class="cost-item">
            <span class="cost-num">{{ detail.token_summary.total_used }}</span>
            <span class="cost-label">总 token</span>
          </div>
          <div class="cost-item">
            <span class="cost-num">¥{{ detail.cost_estimate ?? 0 }}</span>
            <span class="cost-label">成本估算</span>
          </div>
        </div>

        <div class="budget-list">
          <div
            v-for="s in detail.token_summary.by_stage || []"
            :key="s.stage"
            class="budget-item"
          >
            <span class="b-label">{{ s.stage }}</span>
            <el-progress
              :percentage="Math.min(s.usage_pct, 100)"
              :stroke-width="6"
              :show-text="false"
              :color="s.usage_pct > 80 ? '#f56c6c' : '#409eff'"
            />
            <span class="b-value">{{ s.used }}/{{ s.limit }}</span>
          </div>
        </div>
      </el-card>

      <!-- 专家论证要点 -->
      <el-card v-if="detail?.expert_points?.length" shadow="never" class="expert-card">
        <template #header>
          <span class="pane-title">
            专家论证要点
            <el-tag size="small" type="warning" effect="plain" class="ml-4">
              {{ detail.expert_points?.length || 0 }} 条
            </el-tag>
          </span>
        </template>
        <div
          v-for="(p, i) in detail.expert_points || []"
          :key="i"
          class="expert-point"
        >
          <div class="ep-head">
            <span class="ep-index">{{ i + 1 }}</span>
            <span class="ep-title">{{ p.point_title }}</span>
            <el-tag
              v-if="p.risk_level"
              size="small"
              :type="p.risk_level === 'high' ? 'danger' : p.risk_level === 'medium' ? 'warning' : 'info'"
              effect="plain"
            >
              {{ p.risk_level === "high" ? "高风险" : p.risk_level === "medium" ? "中风险" : "低风险" }}
            </el-tag>
          </div>
          <p class="ep-content">{{ p.point_content }}</p>
        </div>
      </el-card>
    </aside>
  </div>
</template>

<style scoped lang="scss">
.review-workbench {
  display: grid;
  grid-template-columns: 340px 1fr 340px;
  gap: 12px;
  height: 100%;
  padding: 12px;
  background: var(--el-bg-color-page);
}

.pane {
  overflow-y: auto;
  min-height: 0;
}

.pane-title {
  font-size: 14px;
  font-weight: 500;
}

.pane-left,
.pane-right {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* ---- 左栏 ---- */

.input-card :deep(.el-card__body) {
  padding: 14px;
}

.submit-btn {
  width: 100%;
}

.mode-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  width: 100%;
}

.mode-tip {
  font-size: 11.5px;
  color: var(--el-text-color-placeholder);
  line-height: 1.5;
}

.task-progress {
  width: 100%;
}

.tp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 5px;
}

.tp-stage {
  font-size: 12px;
  color: var(--el-color-primary);
  font-weight: 500;
}

.tp-pct {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.hint {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
  padding: 8px 10px;
  background: var(--el-fill-color-light);
  border-radius: 4px;

  .el-icon {
    margin-top: 2px;
    flex-shrink: 0;
  }
}

.verdict-card :deep(.el-card__body) {
  padding: 14px;
}

.verdict-row {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}

.categories {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}

.thresholds {
  margin-bottom: 12px;

  .thresholds-title {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    margin-bottom: 6px;
  }

  .threshold-item {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--el-text-color-regular);
    padding: 4px 0;
    line-height: 1.5;
  }
}

.score-block {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);

  .score {
    display: flex;
    align-items: baseline;
    gap: 2px;

    .score-num {
      font-size: 28px;
      font-weight: 600;
      color: var(--el-color-primary);
      font-variant-numeric: tabular-nums;
    }

    .score-unit {
      font-size: 12px;
      color: var(--el-text-color-secondary);
    }
  }
}

.history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12.5px;
  transition: background 0.2s;

  &:hover {
    background: var(--el-fill-color-light);
  }

  &.active {
    background: var(--el-color-primary-light-9);
    color: var(--el-color-primary);
  }

  .h-title {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .h-score {
    color: var(--el-text-color-secondary);
    flex-shrink: 0;
  }
}

/* ---- 中栏 ---- */

.pane-center {
  display: flex;
  flex-direction: column;
  background: var(--el-bg-color);
  border-radius: 4px;
  padding: 14px;
}

.center-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  gap: 12px;

  h3 {
    margin: 0;
    font-size: 15px;
    font-weight: 500;
    flex-shrink: 0;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    justify-content: flex-end;
  }
}

.stat-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;

  .stat {
    flex: 1;
    min-width: 62px;
    text-align: center;
    padding: 8px 4px;
    border-radius: 4px;
    background: var(--el-fill-color-light);

    .num {
      display: block;
      font-size: 18px;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
      line-height: 1.2;
    }

    .label {
      font-size: 11px;
      color: var(--el-text-color-secondary);
    }

    &.ok .num { color: var(--el-color-success); }
    &.danger .num { color: var(--el-color-danger); }
    &.warn .num { color: var(--el-color-warning); }
    &.cite .num { color: var(--el-color-primary); }
    &.refuse .num { color: var(--el-color-warning); }
  }
}

.refuse-alert {
  margin-bottom: 12px;
}

.items-scroll {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

/* ---- 右栏 ---- */

.trace-card :deep(.el-card__body) {
  height: 100%;
  max-height: 420px;
  overflow-y: auto;
}

.cost-card :deep(.el-card__body),
.expert-card :deep(.el-card__body) {
  padding: 14px;
}

.cost-summary {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;

  .cost-item {
    flex: 1;
    text-align: center;

    .cost-num {
      display: block;
      font-size: 20px;
      font-weight: 600;
      color: var(--el-color-primary);
      font-variant-numeric: tabular-nums;
    }

    .cost-label {
      font-size: 11px;
      color: var(--el-text-color-secondary);
    }
  }
}

.budget-item {
  display: grid;
  grid-template-columns: 90px 1fr 70px;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 11px;

  .b-label {
    color: var(--el-text-color-secondary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .b-value {
    text-align: right;
    color: var(--el-text-color-secondary);
    font-variant-numeric: tabular-nums;
  }
}

.expert-point {
  padding-bottom: 10px;
  margin-bottom: 10px;
  border-bottom: 1px dashed var(--el-border-color-lighter);

  &:last-child {
    border-bottom: none;
    margin-bottom: 0;
    padding-bottom: 0;
  }

  .ep-head {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
  }

  .ep-index {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: var(--el-color-warning);
    color: #fff;
    font-size: 11px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .ep-title {
    font-size: 12.5px;
    font-weight: 500;
    flex: 1;
  }

  .ep-content {
    margin: 0 0 0 22px;
    font-size: 12px;
    line-height: 1.6;
    color: var(--el-text-color-secondary);
  }
}

.ml-4 {
  margin-left: 4px;
}

@media (max-width: 1400px) {
  .review-workbench {
    grid-template-columns: 300px 1fr 300px;
  }
}
</style>