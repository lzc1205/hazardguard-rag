<script setup lang="ts">
/**
 * 项目详情（需求文档 7.4.4）。
 *
 * 三栏：基本信息 / 项目文档 / 审查记录。
 * 审查员从这里能一站式看完「这个项目交了什么文件、审了什么、结论如何」。
 */
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { projectApi } from "@/api";
import { CONCLUSION_META } from "@/utils/constants";

const route = useRoute();
const router = useRouter();
const loading = ref(false);
const project = ref<any>(null);
const documents = ref<any[]>([]);
const reviews = ref<any[]>([]);

const STATUS_META: Record<string, { label: string; type: any }> = {
  draft: { label: "草稿", type: "info" },
  in_review: { label: "审查中", type: "warning" },
  reviewed: { label: "已完成", type: "success" },
  archived: { label: "已归档", type: "gray" },
};

function fmtDate(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

async function load() {
  loading.value = true;
  try {
    const id = route.params.id as string;
    const [detail, docs, revs] = await Promise.all([
      projectApi.detail(id),
      projectApi.documents(id),
      projectApi.reviews(id),
    ]);
    project.value = detail.data;
    documents.value = docs.data.items || [];
    reviews.value = revs.data.items || [];
  } catch {
    ElMessage.error("加载项目详情失败");
  } finally {
    loading.value = false;
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
        <h2>{{ project?.name || "项目详情" }}</h2>
        <el-tag
          v-if="project"
          :type="STATUS_META[project.status]?.type || 'info'"
          size="small"
          effect="plain"
        >
          {{ project.status_label }}
        </el-tag>
      </div>
      <div class="head-actions">
        <el-button size="small" @click="router.push(`/documents?project=${project?.id}`)">
          <el-icon><Document /></el-icon>项目文档
        </el-button>
        <el-button
          type="primary"
          size="small"
          @click="router.push({ path: '/review', query: { project_id: project?.id } })"
        >
          <el-icon><Checked /></el-icon>发起审查
        </el-button>
      </div>
    </div>

    <el-row v-if="project" :gutter="12">
      <el-col :span="8">
        <el-card shadow="never" class="panel">
          <template #header><span>项目信息</span></template>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="项目编号">
              {{ project.code || "-" }}
            </el-descriptions-item>
            <el-descriptions-item label="施工单位">
              {{ project.construction_unit || "-" }}
            </el-descriptions-item>
            <el-descriptions-item label="监理单位">
              {{ project.supervision_unit || "-" }}
            </el-descriptions-item>
            <el-descriptions-item label="项目地点">
              {{ project.location || "-" }}
            </el-descriptions-item>
            <el-descriptions-item label="创建时间">
              {{ fmtDate(project.created_at) }}
            </el-descriptions-item>
            <el-descriptions-item label="项目描述">
              {{ project.description || "-" }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-card shadow="never" class="panel mt-12">
          <template #header><span>合规概况</span></template>
          <div class="ov-row">
            <div class="ov-item">
              <div class="ov-value">{{ project.document_count }}</div>
              <div class="ov-label">方案文档</div>
            </div>
            <div class="ov-item">
              <div class="ov-value">{{ project.review_count }}</div>
              <div class="ov-label">审查记录</div>
            </div>
            <div class="ov-item" :class="{ danger: project.over_scale_count > 0 }">
              <div class="ov-value">{{ project.over_scale_count }}</div>
              <div class="ov-label">超规模</div>
            </div>
          </div>
          <el-alert
            v-if="project.over_scale_count > 0"
            type="warning"
            :closable="false"
            class="mt-10"
          >
            本项目存在超规模危大工程，须组织专家论证
          </el-alert>
        </el-card>
      </el-col>

      <el-col :span="16">
        <el-card shadow="never" class="panel">
          <template #header>
            <div class="panel-head">
              <span>项目文档</span>
              <el-button
                link
                type="primary"
                size="small"
                @click="router.push(`/documents?project=${project.id}`)"
              >
                管理文档
              </el-button>
            </div>
          </template>
          <el-table :data="documents" size="small" empty-text="该项目还没有上传文档" max-height="240">
            <el-table-column label="文档" min-width="200">
              <template #default="{ row }">
                <span class="link" @click="router.push(`/documents/${row.id}`)">
                  {{ row.title }}
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="file_type" label="类型" width="70" />
            <el-table-column label="解析状态" width="90">
              <template #default="{ row }">
                <el-tag
                  :type="row.parse_status === 'parsed' ? 'success' : 'info'"
                  size="small"
                  effect="plain"
                >
                  {{ row.parse_status === "parsed" ? "已解析" : row.parse_status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="上传时间" width="160">
              <template #default="{ row }">
                <span class="dim">{{ fmtDate(row.created_at) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="never" class="panel mt-12">
          <template #header><span>审查记录</span></template>
          <el-table :data="reviews" size="small" empty-text="该项目还没有审查记录" max-height="280">
            <el-table-column label="审查项" min-width="200">
              <template #default="{ row }">
                <span class="link" @click="router.push(`/review?id=${row.id}`)">
                  {{ row.title }}
                </span>
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
            <el-table-column label="评分" width="70">
              <template #default="{ row }">
                <span class="score">{{ row.overall_score ?? "-" }}</span>
              </template>
            </el-table-column>
            <el-table-column label="超规模" width="90">
              <template #default="{ row }">
                <el-tag v-if="row.is_over_scale" type="danger" size="small" effect="plain">
                  超规模
                </el-tag>
                <span v-else class="dim">-</span>
              </template>
            </el-table-column>
            <el-table-column label="时间" width="160">
              <template #default="{ row }">
                <span class="dim">{{ fmtDate(row.created_at) }}</span>
              </template>
            </el-table-column>
          </el-table>
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
}

.mt-12 {
  margin-top: 12px;
}

.mt-10 {
  margin-top: 10px;
}

.ov-row {
  display: flex;
  gap: 10px;
}

.ov-item {
  flex: 1;
  text-align: center;
  padding: 10px 6px;
  background: var(--el-fill-color-light);
  border-radius: 6px;

  .ov-value {
    font-size: 19px;
    font-weight: 500;
    color: var(--el-color-primary);
    font-variant-numeric: tabular-nums;
    line-height: 1.2;
  }

  .ov-label {
    font-size: 11.5px;
    color: var(--el-text-color-secondary);
    margin-top: 2px;
  }

  &.danger .ov-value {
    color: var(--el-color-danger);
  }
}

.link {
  color: var(--el-color-primary);
  cursor: pointer;
  font-size: 12.5px;

  &:hover {
    text-decoration: underline;
  }
}

.score {
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.dim {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>