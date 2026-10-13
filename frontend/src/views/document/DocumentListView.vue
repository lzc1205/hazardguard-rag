<script setup lang="ts">
/**
 * 文档管理页（需求文档 7.4.5）。
 *
 * 这是「上传方案 → 审查 → 出报告」闭环的入口。
 * 之前 documents 表有 0 行，根因就是前端没有上传入口——
 * 只能粘贴文本，真实使用场景走不通。
 */
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { documentApi, projectApi, type ProjectItem } from "@/api";

const router = useRouter();
const loading = ref(false);
const uploading = ref(false);
const uploadPercent = ref(0);

const docs = ref<any[]>([]);
const projects = ref<ProjectItem[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = 10;
const filterProject = ref<string>("");
const filterStatus = ref<string>("");

const uploadVisible = ref(false);
const uploadForm = ref({
  title: "",
  project_id: "",
  doc_category: "",
  construction_unit: "",
});

/** 解析状态 → 标签与配色 */
const PARSE_META: Record<string, { label: string; type: any }> = {
  pending: { label: "待解析", type: "info" },
  parsing: { label: "解析中", type: "warning" },
  parsed: { label: "已解析", type: "success" },
  failed: { label: "解析失败", type: "danger" },
  ocr_needed: { label: "需 OCR", type: "warning" },
};

const CATEGORY_OPTIONS = [
  { value: "foundation_pit", label: "基坑工程" },
  { value: "formwork", label: "模板工程及支撑体系" },
  { value: "lifting", label: "起重吊装及安装拆卸" },
  { value: "scaffold", label: "脚手架工程" },
  { value: "demolition", label: "拆除爆破工程" },
  { value: "underground", label: "暗挖工程" },
  { value: "other", label: "其他" },
];

const fileTypeIcon = (t: string) =>
  ({ pdf: "PDF", docx: "DOC", doc: "DOC", txt: "TXT" }[t?.toLowerCase()] ?? t?.toUpperCase() ?? "?");

function fmtSize(bytes: number | null): string {
  if (!bytes) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function fmtTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

async function load() {
  loading.value = true;
  try {
    const res = await documentApi.list({
      page: page.value,
      page_size: pageSize,
      project_id: filterProject.value || undefined,
      parse_status: filterStatus.value || undefined,
    } as any);
    docs.value = res.data.items || [];
    total.value = res.data.total || 0;
  } catch {
    ElMessage.error("加载文档列表失败");
  } finally {
    loading.value = false;
  }
}

async function loadProjects() {
  try {
    const res = await projectApi.list({ page_size: 100 });
    projects.value = res.data.items || [];
  } catch {
    // 项目列表失败不阻塞文档列表
  }
}

/** 选中的原始 File 对象（上传时必须传这个，不是 el-upload 的包装对象） */
const selectedFile = ref<File | null>(null);

async function doUpload() {
  if (!selectedFile.value) {
    ElMessage.warning("请先选择方案文件");
    return;
  }
  if (!uploadForm.value.title.trim()) {
    ElMessage.warning("请填写文档标题");
    return;
  }
  uploading.value = true;
  uploadPercent.value = 0;
  try {
    const res = await documentApi.upload(
      selectedFile.value,
      {
        title: uploadForm.value.title,
        project_id: uploadForm.value.project_id || undefined,
        doc_category: uploadForm.value.doc_category || undefined,
        construction_unit: uploadForm.value.construction_unit || undefined,
      },
      (p) => (uploadPercent.value = p),
    );
    // 解析失败也算上传成功（文档已入库），但必须明确告知原因
    const parsed = res.data?.parse_status === "parsed";
    if (parsed) {
      ElMessage.success("上传并解析完成");
    } else {
      ElMessage.warning({
        message: "文档已上传，但解析未成功，无法发起审查。请检查文件格式或内容",
        duration: 6000,
        showClose: true,
      });
    }
    uploadVisible.value = false;
    uploadForm.value = { title: "", project_id: "", doc_category: "", construction_unit: "" };
    selectedFile.value = null;
    page.value = 1;
    load();
  } catch (err: any) {
    // 真实原因已由 request 拦截器统一弹窗，这里只补充上下文
    ElMessage.error({
      message: `上传失败：${err?.message || "未知原因"}`,
      duration: 6000,
      showClose: true,
    });
  } finally {
    uploading.value = false;
  }
}

function onFileChange(file: any) {
  selectedFile.value = file?.raw ?? null;
  // 标题默认取文件名（去掉扩展名）
  if (!uploadForm.value.title && file?.name) {
    uploadForm.value.title = file.name.replace(/\.[^.]+$/, "");
  }
  // 从文件名前缀推断施工单位，方便填写
  if (!uploadForm.value.construction_unit && file?.name) {
    const m = String(file.name).match(/^(.+?)[-_]/);
    if (m) uploadForm.value.construction_unit = m[1];
  }
}

async function removeDoc(row: any) {
  try {
    await ElMessageBox.confirm(
      `确认删除「${row.title}」？该操作不可撤销，且该文档的历史审查记录将无法追溯。`,
      "删除文档",
      { type: "warning", confirmButtonText: "确认删除", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  try {
    await documentApi.remove(row.id);
    ElMessage.success("已删除");
    load();
  } catch {
    ElMessage.error("删除失败");
  }
}

/** 文档已解析才能发起审查——这是必要的前置校验 */
function canReview(row: any) {
  return row.parse_status === "parsed";
}

function goReview(row: any) {
  router.push({
    path: "/review",
    query: { document_id: row.id, title: row.title },
  });
}

const emptyText = computed(() => {
  if (docs.value.length === 0) return "还没有文档，点击右上角「上传文档」开始";
  return "没有匹配的文档";
});

onMounted(async () => {
  await Promise.all([load(), loadProjects()]);
});
</script>

<template>
  <div class="doc-page">
    <!-- 顶部操作条 -->
    <el-card shadow="never" class="toolbar">
      <div class="tb-row">
        <div class="tb-left">
          <span class="tb-label">所属项目</span>
          <el-select
            v-model="filterProject"
            placeholder="全部项目"
            clearable
            size="small"
            style="width: 190px"
            @change="() => { page = 1; load(); }"
          >
            <el-option
              v-for="p in projects"
              :key="p.id"
              :label="p.name"
              :value="p.id"
            />
          </el-select>

          <span class="tb-label">解析状态</span>
          <el-select
            v-model="filterStatus"
            placeholder="全部"
            clearable
            size="small"
            style="width: 130px"
            @change="() => { page = 1; load(); }"
          >
            <el-option
              v-for="(m, k) in PARSE_META"
              :key="k"
              :label="m.label"
              :value="k"
            />
          </el-select>
        </div>
        <div class="tb-right">
          <el-button size="small" @click="load">
            <el-icon><Refresh /></el-icon>刷新
          </el-button>
          <el-button type="primary" size="small" @click="uploadVisible = true">
            <el-icon><Upload /></el-icon>上传文档
          </el-button>
        </div>
      </div>
    </el-card>

    <!-- 文档表格 -->
    <el-card shadow="never" class="table-card">
      <el-table :data="docs" v-loading="loading" :empty-text="emptyText" size="small">
        <el-table-column label="文档" min-width="260">
          <template #default="{ row }">
            <div class="doc-cell">
              <el-tag :type="row.file_type === 'pdf' ? 'danger' : 'primary'" size="small" effect="plain">
                {{ fileTypeIcon(row.file_type) }}
              </el-tag>
              <div class="doc-info">
                <div class="doc-title">{{ row.title }}</div>
                <div class="doc-file">{{ row.file_name }}</div>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="所属项目" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="dim">{{ projects.find((p) => p.id === row.project_id)?.name || "未关联" }}</span>
          </template>
        </el-table-column>

        <el-table-column label="解析状态" width="105">
          <template #default="{ row }">
            <el-tag
              :type="PARSE_META[row.parse_status]?.type || 'info'"
              size="small"
              effect="plain"
            >
              {{ PARSE_META[row.parse_status]?.label || row.parse_status }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="页数/字数" width="105">
          <template #default="{ row }">
            <span v-if="row.page_count || row.word_count" class="dim">
              {{ row.page_count ? `${row.page_count} 页` : "" }}
              {{ row.word_count ? ` / ${row.word_count} 字` : "" }}
            </span>
            <span v-else class="dim">-</span>
          </template>
        </el-table-column>

        <el-table-column label="大小" width="85">
          <template #default="{ row }">
            <span class="dim">{{ fmtSize(row.file_size) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="上传时间" width="160">
          <template #default="{ row }">
            <span class="dim">{{ fmtTime(row.created_at) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              size="small"
              :disabled="!canReview(row)"
              @click="goReview(row)"
            >
              发起审查
            </el-button>
            <el-button link size="small" @click="router.push(`/documents/${row.id}`)">
              详情
            </el-button>
            <el-button link type="danger" size="small" @click="removeDoc(row)">
              删除
            </el-button>
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

    <!-- 上传对话框 -->
    <el-dialog v-model="uploadVisible" title="上传专项施工方案" width="520px">
      <el-form label-width="88px">
        <el-form-item label="方案文件" required>
          <el-upload
            class="up-area"
            :auto-upload="false"
            :limit="1"
            :on-change="onFileChange"
            accept=".pdf,.docx,.txt"
            drag
          >
            <div class="up-inner">
              <el-icon class="up-icon"><UploadFilled /></el-icon>
              <div>拖拽文件到此处，或<em>点击选择</em></div>
              <div class="up-tip">
                仅支持 PDF / .docx / .txt，单文件 ≤
                {{ 50 }}MB
              </div>
              <div class="up-warn">
                Word 请在文件里「另存为 .docx」；旧版 .doc 与 WPS 格式需先转换
              </div>
            </div>
          </el-upload>
        </el-form-item>
        <el-form-item label="文档标题" required>
          <el-input
            v-model="uploadForm.title"
            placeholder="如：深基坑开挖专项施工方案"
          />
        </el-form-item>
        <el-form-item label="所属项目">
          <el-select
            v-model="uploadForm.project_id"
            placeholder="不关联项目"
            clearable
            style="width: 100%"
          >
            <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
          <div class="form-tip">关联项目后可在项目详情中查看该文档</div>
        </el-form-item>
        <el-form-item label="危大类别">
          <el-select
            v-model="uploadForm.doc_category"
            placeholder="未指定"
            clearable
            style="width: 100%"
          >
            <el-option
              v-for="c in CATEGORY_OPTIONS"
              :key="c.value"
              :label="c.label"
              :value="c.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="施工单位">
          <el-input v-model="uploadForm.construction_unit" placeholder="选填" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="doUpload">
          {{ uploading ? `上传中 ${uploadPercent}%` : "开始上传" }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.doc-page {
  padding: 12px;
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
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.tb-left {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.tb-label {
  font-size: 12.5px;
  color: var(--el-text-color-secondary);
}

.table-card :deep(.el-card__body) {
  padding: 0;
}

.doc-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.doc-info {
  min-width: 0;
}

.doc-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-file {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

.up-area {
  width: 100%;

  :deep(.el-upload-dragger) {
    padding: 20px 10px;
  }
}

.up-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  color: var(--el-text-color-regular);

  .up-icon {
    font-size: 34px;
    color: var(--el-color-primary);
  }

  .up-tip {
    font-size: 11.5px;
    color: var(--el-text-color-placeholder);
  }

  .up-warn {
    font-size: 11px;
    color: var(--el-color-warning);
    margin-top: 2px;
  }
}

.form-tip {
  font-size: 11.5px;
  color: var(--el-text-color-placeholder);
  margin-top: 3px;
  line-height: 1.5;
}
</style>