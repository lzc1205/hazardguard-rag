<script setup lang="ts">
/**
 * 文档详情页（需求文档 7.4.6）。
 *
 * 重点是让审查员确认「解析结果对不对」——
 * PDF 解析成什么样、危大参数有没有抽出来，直接决定审查结论。
 */
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { documentApi, reviewApi } from "@/api";

const route = useRoute();
const router = useRouter();
const loading = ref(false);
const doc = ref<any>(null);
const reviews = ref<any[]>([]);

const PARSE_META: Record<string, { label: string; type: any }> = {
  pending: { label: "待解析", type: "info" },
  parsing: { label: "解析中", type: "warning" },
  parsed: { label: "已解析", type: "success" },
  failed: { label: "解析失败", type: "danger" },
  ocr_needed: { label: "需 OCR", type: "warning" },
};

const CATEGORY_LABEL: Record<string, string> = {
  foundation_pit: "基坑工程",
  formwork: "模板工程及支撑体系",
  lifting: "起重吊装及安装拆卸",
  scaffold: "脚手架工程",
  demolition: "拆除爆破工程",
  underground: "暗挖工程",
  special_plan: "专项施工方案",
  other: "其他",
};

function fmtSize(b: number | null): string {
  if (!b) return "-";
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

function fmtTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

/** 从解析正文里粗提关键参数，供审查员快速核对 */
const extracted = computed(() => {
  const text = doc.value?.content_preview || "";
  if (!text) return [];
  const patterns: Array<[string, RegExp]> = [
    ["开挖深度", /开挖深度\s*[约=:：]?\s*([\d.]+)\s*m/],
    ["基坑周长", /周长\s*[约=:：]?\s*([\d.]+)\s*m/],
    ["模板高度", /(?:搭设高度|高度)\s*[约=:：]?\s*([\d.]+)\s*m/],
    ["起重量", /起重量?\s*[约=:：]?\s*([\d.]+)\s*t/],
    ["结构高度", /结构高度\s*[约=:：]?\s*([\d.]+)\s*m/],
  ];
  const out: Array<{ label: string; value: string }> = [];
  for (const [label, re] of patterns) {
    const m = text.match(re);
    if (m) out.push({ label, value: `${m[1]} m` });
  }
  return out;
});

async function load() {
  loading.value = true;
  try {
    const id = route.params.id as string;
    const res = await documentApi.detail(id);
    doc.value = res.data;
    // 带上该文档的历史审查
    const list = await reviewApi.list({ page_size: 50 } as any);
    reviews.value = (list.data.items || []).filter(
      (r: any) => r.document_id === id,
    );
  } catch {
    ElMessage.error("加载文档详情失败");
  } finally {
    loading.value = false;
  }
}

function goReview() {
  router.push({
    path: "/review",
    query: { document_id: doc.value.id, title: doc.value.title },
  });
}

async function download() {
  try {
    const res: any = await documentApi.download(doc.value.id);
    const url = URL.createObjectURL(new Blob([res]));
    const a = document.createElement("a");
    a.href = url;
    a.download = doc.value.file_name || "document";
    a.click();
    URL.revokeObjectURL(url);
  } catch {
    ElMessage.error("下载失败");
  }
}

onMounted(load);
</script>

<template>
  <div class="detail-page" v-loading="loading">
    <div class="page-head">
      <el-button size="small" @click="router.back()">
        <el-icon><ArrowLeft /></el-icon>返回
      </el-button>
      <div class="head-title">
        <h2>{{ doc?.title || "文档详情" }}</h2>
        <el-tag
          v-if="doc"
          :type="PARSE_META[doc.parse_status]?.type || 'info'"
          size="small"
          effect="plain"
        >
          {{ PARSE_META[doc.parse_status]?.label || doc.parse_status }}
        </el-tag>
      </div>
      <div class="head-actions">
        <el-button size="small" @click="download">
          <el-icon><Download /></el-icon>下载原文件
        </el-button>
        <el-button
          type="primary"
          size="small"
          :disabled="doc?.parse_status !== 'parsed'"
          @click="goReview"
        >
          <el-icon><Checked /></el-icon>发起审查
        </el-button>
      </div>
    </div>

    <el-row v-if="doc" :gutter="12">
      <!-- 左侧：元信息 + 参数提取 -->
      <el-col :span="9">
        <el-card shadow="never" class="panel">
          <template #header><span>文档信息</span></template>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="文件名">{{ doc.file_name }}</el-descriptions-item>
            <el-descriptions-item label="文件类型">
              {{ (doc.file_type || '').toUpperCase() }} · {{ fmtSize(doc.file_size) }}
            </el-descriptions-item>
            <el-descriptions-item label="危大类别">
              {{ CATEGORY_LABEL[doc.doc_category] || doc.doc_category || "未指定" }}
            </el-descriptions-item>
            <el-descriptions-item label="施工单位">
              {{ doc.construction_unit || "-" }}
            </el-descriptions-item>
            <el-descriptions-item label="页数">{{ doc.page_count ?? "-" }}</el-descriptions-item>
            <el-descriptions-item label="字数">
              {{ doc.word_count ?? "-" }}
            </el-descriptions-item>
            <el-descriptions-item label="上传时间">
              {{ fmtTime(doc.created_at) }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <!-- 关键参数：审查员核对解析质量的第一眼 -->
        <el-card shadow="never" class="panel mt-12">
          <template #header>
            <div class="panel-head">
              <span>识别到的关键参数</span>
              <el-tag size="small" type="info" effect="plain">正则粗提取，仅供参考</el-tag>
            </div>
          </template>
          <div v-if="extracted.length" class="params">
            <div v-for="p in extracted" :key="p.label" class="param-row">
              <span class="p-label">{{ p.label }}</span>
              <span class="p-value">{{ p.value }}</span>
            </div>
          </div>
          <div v-else class="empty-tip">
            未从正文识别到关键参数。<br />
            若方案中确实写了，可在审查工作台手动补充。
          </div>
        </el-card>

        <!-- 该文档的历史审查 -->
        <el-card shadow="never" class="panel mt-12">
          <template #header>
            <span>历史审查记录</span>
          </template>
          <div v-if="reviews.length" class="rev-list">
            <div
              v-for="r in reviews"
              :key="r.id"
              class="rev-item"
              @click="router.push(`/review?id=${r.id}`)"
            >
              <div class="rev-title">{{ r.title }}</div>
              <div class="rev-meta">
                <el-tag size="small" effect="plain">{{ r.overall_score ?? "-" }} 分</el-tag>
                <span class="dim">{{ fmtTime(r.created_at) }}</span>
              </div>
            </div>
          </div>
          <div v-else class="empty-tip">该文档还没有审查记录</div>
        </el-card>
      </el-col>

      <!-- 右侧：解析正文 -->
      <el-col :span="15">
        <el-card shadow="never" class="panel">
          <template #header>
            <div class="panel-head">
              <span>解析后的正文</span>
              <span class="dim">
                {{ doc.content_length }} 字（预览前 20000 字）
              </span>
            </div>
          </template>
          <pre v-if="doc.content_preview" class="content">{{ doc.content_preview }}</pre>
          <div v-else class="empty-tip">
            未能解析出正文内容。<br />
            常见原因：扫描件无文本层（需 OCR）、加密 PDF、纯图片格式。
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped lang="scss">
.detail-page {
  padding: 12px;
}

.page-head {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.head-title {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;

  h2 {
    margin: 0;
    font-size: 16px;
    font-weight: 500;
    color: var(--el-text-color-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.head-actions {
  display: flex;
  gap: 8px;
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

.params {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.param-row {
  display: flex;
  justify-content: space-between;
  padding: 6px 10px;
  background: var(--el-fill-color-light);
  border-radius: 5px;
  font-size: 12.5px;

  .p-label {
    color: var(--el-text-color-secondary);
  }
  .p-value {
    font-weight: 500;
    color: var(--el-color-primary);
    font-variant-numeric: tabular-nums;
  }
}

.rev-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.rev-item {
  padding: 7px 10px;
  border-radius: 5px;
  border: 1px solid var(--el-border-color-lighter);
  cursor: pointer;
  transition: border-color 0.15s;

  &:hover {
    border-color: var(--el-color-primary-light-5);
  }

  .rev-title {
    font-size: 12.5px;
    font-weight: 500;
    margin-bottom: 3px;
  }

  .rev-meta {
    display: flex;
    align-items: center;
    gap: 8px;
  }
}

.content {
  margin: 0;
  padding: 12px;
  background: var(--el-fill-color-lighter);
  border-radius: 6px;
  font-family: "Microsoft YaHei", Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 620px;
  overflow-y: auto;
}

.empty-tip {
  font-size: 12.5px;
  color: var(--el-text-color-placeholder);
  line-height: 1.8;
  text-align: center;
  padding: 20px 0;
}

.dim {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>