<script setup lang="ts">
/**
 * 项目管理列表（需求文档 7.4.3）。
 *
 * 项目是「文档 → 审查 → 报告」的归属单位。
 * 之前 projects 表 0 行，根因就是这层缺失——
 * 审查记录无法按项目归集，真实使用场景无法落地。
 */
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { projectApi, type ProjectItem } from "@/api";

const router = useRouter();
const loading = ref(false);
const projects = ref<ProjectItem[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = 12;
const keyword = ref("");
const filterStatus = ref("");

const dialogVisible = ref(false);
const editing = ref<ProjectItem | null>(null);
const form = ref({
  name: "",
  code: "",
  construction_unit: "",
  supervision_unit: "",
  location: "",
  description: "",
});

const STATUS_META: Record<string, { label: string; type: any }> = {
  draft: { label: "草稿", type: "info" },
  in_review: { label: "审查中", type: "warning" },
  reviewed: { label: "已完成", type: "success" },
  archived: { label: "已归档", type: "gray" },
};

const stats = computed(() => {
  const all = projects.value;
  return {
    total: total.value,
    overScale: all.reduce((s, p) => s + p.over_scale_count, 0),
    docs: all.reduce((s, p) => s + p.document_count, 0),
    reviews: all.reduce((s, p) => s + p.review_count, 0),
  };
});

function fmtDate(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleDateString("zh-CN");
}

async function load() {
  loading.value = true;
  try {
    const res = await projectApi.list({
      page: page.value,
      page_size: pageSize,
      keyword: keyword.value || undefined,
      status: filterStatus.value || undefined,
    });
    projects.value = res.data.items || [];
    total.value = res.data.total || 0;
  } catch {
    ElMessage.error("加载项目列表失败");
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  editing.value = null;
  form.value = {
    name: "",
    code: "",
    construction_unit: "",
    supervision_unit: "",
    location: "",
    description: "",
  };
  dialogVisible.value = true;
}

function openEdit(p: ProjectItem) {
  editing.value = p;
  form.value = {
    name: p.name,
    code: p.code || "",
    construction_unit: p.construction_unit || "",
    supervision_unit: p.supervision_unit || "",
    location: p.location || "",
    description: p.description || "",
  };
  dialogVisible.value = true;
}

async function save() {
  if (!form.value.name.trim()) {
    ElMessage.warning("请填写项目名称");
    return;
  }
  try {
    if (editing.value) {
      await projectApi.update(editing.value.id, { ...form.value });
      ElMessage.success("已保存");
    } else {
      await projectApi.create({ ...form.value });
      ElMessage.success("项目已创建");
    }
    dialogVisible.value = false;
    load();
  } catch {
    ElMessage.error("保存失败");
  }
}

async function remove(p: ProjectItem) {
  // 删除会级联影响文档与审查，必须提示实际影响数量
  const msg =
    p.document_count || p.review_count
      ? `项目「${p.name}」下有 ${p.document_count} 份文档、${p.review_count} 条审查记录，删除后一并失效且不可恢复。确认删除？`
      : `确认删除项目「${p.name}」？`;
  try {
    await ElMessageBox.confirm(msg, "删除项目", {
      type: "warning",
      confirmButtonText: "确认删除",
      cancelButtonText: "取消",
    });
  } catch {
    return;
  }
  try {
    await projectApi.remove(p.id);
    ElMessage.success("已删除");
    load();
  } catch {
    ElMessage.error("删除失败");
  }
}

onMounted(load);
</script>

<template>
  <div class="proj-page">
    <!-- 概览条 -->
    <el-row :gutter="10" class="stat-row">
      <el-col :span="6">
        <div class="stat-box">
          <div class="s-value">{{ stats.total }}</div>
          <div class="s-label">项目总数</div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="stat-box">
          <div class="s-value">{{ stats.docs }}</div>
          <div class="s-label">方案文档</div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="stat-box">
          <div class="s-value">{{ stats.reviews }}</div>
          <div class="s-label">审查记录</div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="stat-box warn">
          <div class="s-value">{{ stats.overScale }}</div>
          <div class="s-label">超规模工程</div>
        </div>
      </el-col>
    </el-row>

    <!-- 工具条 -->
    <el-card shadow="never" class="toolbar">
      <div class="tb-row">
        <div class="tb-left">
          <el-input
            v-model="keyword"
            placeholder="搜索项目名/编号/施工单位"
            clearable
            size="small"
            style="width: 240px"
            @keyup.enter="() => { page = 1; load(); }"
            @clear="() => { page = 1; load(); }"
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
          <el-select
            v-model="filterStatus"
            placeholder="全部状态"
            clearable
            size="small"
            style="width: 130px"
            @change="() => { page = 1; load(); }"
          >
            <el-option
              v-for="(m, k) in STATUS_META"
              :key="k"
              :label="m.label"
              :value="k"
            />
          </el-select>
          <el-button size="small" @click="() => { page = 1; load(); }">
            <el-icon><Search /></el-icon>查询
          </el-button>
        </div>
        <el-button type="primary" size="small" @click="openCreate">
          <el-icon><Plus /></el-icon>新建项目
        </el-button>
      </div>
    </el-card>

    <!-- 项目卡片 -->
    <div v-loading="loading" class="card-grid">
      <el-card
        v-for="p in projects"
        :key="p.id"
        shadow="hover"
        class="proj-card"
        @click="router.push(`/projects/${p.id}`)"
      >
        <div class="pc-head">
          <h3 class="pc-name" :title="p.name">{{ p.name }}</h3>
          <el-tag
            :type="STATUS_META[p.status]?.type || 'info'"
            size="small"
            effect="plain"
          >
            {{ p.status_label }}
          </el-tag>
        </div>
        <div class="pc-code">{{ p.code || "未设置编号" }}</div>
        <div class="pc-meta">
          <div class="pc-row">
            <el-icon><OfficeBuilding /></el-icon>
            <span>{{ p.construction_unit || "施工单位未填" }}</span>
          </div>
          <div class="pc-row">
            <el-icon><Location /></el-icon>
            <span>{{ p.location || "地点未填" }}</span>
          </div>
        </div>
        <div class="pc-stats">
          <div class="ps-item">
            <span class="ps-value">{{ p.document_count }}</span>
            <span class="ps-label">文档</span>
          </div>
          <div class="ps-item">
            <span class="ps-value">{{ p.review_count }}</span>
            <span class="ps-label">审查</span>
          </div>
          <div class="ps-item" :class="{ danger: p.over_scale_count > 0 }">
            <span class="ps-value">{{ p.over_scale_count }}</span>
            <span class="ps-label">超规模</span>
          </div>
          <div class="ps-date">{{ fmtDate(p.created_at) }}</div>
        </div>
        <div class="pc-actions" @click.stop>
          <el-button link type="primary" size="small" @click="router.push(`/projects/${p.id}`)">
            详情
          </el-button>
          <el-button link size="small" @click="openEdit(p)">编辑</el-button>
          <el-button link size="small" @click="router.push(`/documents?project=${p.id}`)">
            文档
          </el-button>
          <el-button link type="danger" size="small" @click="remove(p)">删除</el-button>
        </div>
      </el-card>

      <el-empty
        v-if="!loading && projects.length === 0"
        description="还没有项目，点击右上角「新建项目」开始"
        class="empty"
      />
    </div>

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

    <!-- 新建/编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑项目' : '新建项目'"
      width="560px"
    >
      <el-form label-width="92px">
        <el-form-item label="项目名称" required>
          <el-input v-model="form.name" placeholder="如：郑州地铁6号线一期工程" />
        </el-form-item>
        <el-form-item label="项目编号">
          <el-input v-model="form.code" placeholder="如：ZZ06-01" />
        </el-form-item>
        <el-form-item label="施工单位">
          <el-input v-model="form.construction_unit" />
        </el-form-item>
        <el-form-item label="监理单位">
          <el-input v-model="form.supervision_unit" />
        </el-form-item>
        <el-form-item label="项目地点">
          <el-input v-model="form.location" placeholder="如：河南省郑州市金水区" />
        </el-form-item>
        <el-form-item label="项目描述">
          <el-input v-model="form.description" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">
          {{ editing ? "保存" : "创建" }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.proj-page {
  padding: 12px;
}

.stat-row {
  margin-bottom: 10px;
}

.stat-box {
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px 14px;
  text-align: center;

  .s-value {
    font-size: 22px;
    font-weight: 500;
    color: var(--el-color-primary);
    font-variant-numeric: tabular-nums;
    line-height: 1.2;
  }

  .s-label {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    margin-top: 2px;
  }

  &.warn .s-value {
    color: var(--el-color-warning);
  }
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
  gap: 8px;
  flex-wrap: wrap;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
  min-height: 120px;
}

.proj-card {
  cursor: pointer;
  transition: border-color 0.18s;

  :deep(.el-card__body) {
    padding: 14px;
  }
}

.pc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 3px;
}

.pc-name {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pc-code {
  font-size: 11.5px;
  color: var(--el-text-color-placeholder);
  font-family: Consolas, monospace;
  margin-bottom: 9px;
}

.pc-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 10px;
}

.pc-row {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--el-text-color-secondary);

  .el-icon {
    font-size: 13px;
    flex-shrink: 0;
  }

  span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.pc-stats {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 8px 10px;
  background: var(--el-fill-color-lighter);
  border-radius: 6px;
}

.ps-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  line-height: 1.25;

  .ps-value {
    font-size: 14px;
    font-weight: 500;
    color: var(--el-text-color-primary);
    font-variant-numeric: tabular-nums;
  }

  .ps-label {
    font-size: 10.5px;
    color: var(--el-text-color-placeholder);
  }

  &.danger .ps-value {
    color: var(--el-color-danger);
  }
}

.ps-date {
  margin-left: auto;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}

.pc-actions {
  display: flex;
  justify-content: flex-end;
  gap: 2px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.empty {
  grid-column: 1 / -1;
  padding: 30px 0;
}
</style>