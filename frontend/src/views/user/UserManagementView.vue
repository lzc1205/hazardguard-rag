<script setup lang="ts">
/**
 * 用户管理（需求文档 7.4.17，admin 专属）。
 *
 * RBAC 的可视化面：谁能看、谁能审、谁能改配置。
 * 对应 JD「权限控制」，面试时能点开说明角色划分依据。
 */
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { userApi, ROLE_OPTIONS, type UserItem } from "@/api";
import { useUserStore } from "@/stores/userStore";

const userStore = useUserStore();
const loading = ref(false);
const users = ref<UserItem[]>([]);
const total = ref(0);
const page = ref(1);
const keyword = ref("");

const dialogVisible = ref(false);
const editing = ref<UserItem | null>(null);
const form = ref({
  username: "",
  password: "",
  full_name: "",
  email: "",
  phone: "",
  organization: "",
  title: "",
  role: "viewer",
});

/** 非 admin 看不到这个页面的数据 */
const isAdmin = computed(() => userStore.userInfo?.role === "admin");

const roleStats = computed(() => {
  const m: Record<string, number> = {};
  users.value.forEach((u) => (m[u.role] = (m[u.role] || 0) + 1));
  return ROLE_OPTIONS.map((r) => ({
    ...r,
    count: m[r.value] || 0,
  }));
});

const ROLE_DESC: Record<string, string> = {
  admin: "全部权限：用户管理、系统配置、索引重建",
  supervisor: "审查复核与报告签发，不能改系统配置",
  reviewer: "发起审查、查看全部审查记录与报告",
  technician: "上传文档、查看本部门审查结果",
  viewer: "只读，可查看审查结论与法规速查",
};

function fmtTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

function roleMeta(v: string) {
  return ROLE_OPTIONS.find((r) => r.value === v) || ROLE_OPTIONS[4];
}

async function load() {
  if (!isAdmin.value) return;
  loading.value = true;
  try {
    const res = await userApi.list({
      page: page.value,
      page_size: 20,
      keyword: keyword.value || undefined,
    });
    users.value = res.data.items || [];
    total.value = res.data.total || 0;
  } catch {
    ElMessage.error("加载用户列表失败");
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  editing.value = null;
  form.value = {
    username: "",
    password: "",
    full_name: "",
    email: "",
    phone: "",
    organization: "",
    title: "",
    role: "viewer",
  };
  dialogVisible.value = true;
}

function openEdit(u: UserItem) {
  editing.value = u;
  form.value = {
    username: u.username,
    password: "",
    full_name: u.full_name || "",
    email: u.email || "",
    phone: u.phone || "",
    organization: "",
    title: "",
    role: u.role,
  };
  dialogVisible.value = true;
}

async function save() {
  if (!form.value.username.trim()) {
    ElMessage.warning("请填写用户名");
    return;
  }
  if (!editing.value && form.value.password.length < 6) {
    ElMessage.warning("密码至少 6 位");
    return;
  }
  try {
    if (editing.value) {
      await userApi.update(editing.value.id, {
        full_name: form.value.full_name || undefined,
        email: form.value.email || undefined,
        phone: form.value.phone || undefined,
        role: form.value.role,
      });
      ElMessage.success("已保存");
    } else {
      await userApi.create({
        username: form.value.username,
        password: form.value.password,
        full_name: form.value.full_name || undefined,
        email: form.value.email || undefined,
        role: form.value.role,
      });
      ElMessage.success("用户已创建");
    }
    dialogVisible.value = false;
    load();
  } catch {
    ElMessage.error("保存失败");
  }
}

async function toggleStatus(u: UserItem) {
  try {
    await userApi.setStatus(u.id, !u.is_active);
    ElMessage.success(u.is_active ? "已停用该账号" : "已启用该账号");
    load();
  } catch {
    ElMessage.error("操作失败");
  }
}

async function changeRole(u: UserItem, role: string) {
  try {
    await userApi.setRole(u.id, role);
    ElMessage.success("角色已更新");
    load();
  } catch {
    ElMessage.error("角色更新失败");
  }
}

async function remove(u: UserItem) {
  if (u.id === userStore.userInfo?.id) {
    ElMessage.warning("不能删除自己的账号");
    return;
  }
  try {
    await ElMessageBox.confirm(
      `确认删除用户「${u.full_name || u.username}」？其创建的审查记录将保留，但无法追溯到人。`,
      "删除用户",
      { type: "warning", confirmButtonText: "确认删除", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  try {
    await userApi.remove(u.id);
    ElMessage.success("已删除");
    load();
  } catch {
    ElMessage.error("删除失败");
  }
}

onMounted(load);
</script>

<template>
  <div class="user-page">
    <el-alert
      v-if="!isAdmin"
      type="warning"
      :closable="false"
      show-icon
      title="需要管理员权限"
      description="当前账号无权访问用户管理，请使用 admin 账号登录。"
      class="mb-12"
    />

    <template v-else>
      <!-- 角色分布 -->
      <el-row :gutter="10" class="role-row">
        <el-col :span="4" v-for="r in roleStats" :key="r.value">
          <div class="role-box" :title="ROLE_DESC[r.value]">
            <div class="rb-count" :style="{ color: r.type ? '' : '' }">{{ r.count }}</div>
            <div class="rb-label">{{ r.label }}</div>
          </div>
        </el-col>
        <el-col :span="4">
          <el-button type="primary" size="small" class="add-btn" @click="openCreate">
            <el-icon><Plus /></el-icon>新建用户
          </el-button>
        </el-col>
      </el-row>

      <!-- 权限说明 -->
      <el-card shadow="never" class="perm-card mb-12">
        <template #header><span>角色权限划分</span></template>
        <div class="perm-grid">
          <div v-for="r in ROLE_OPTIONS" :key="r.value" class="perm-item">
            <el-tag :type="r.type as any" size="small" effect="plain">{{ r.label }}</el-tag>
            <span class="perm-desc">{{ ROLE_DESC[r.value] }}</span>
          </div>
        </div>
      </el-card>

      <!-- 用户表 -->
      <el-card shadow="never" class="table-card">
        <div class="tb-head">
          <el-input
            v-model="keyword"
            placeholder="搜索用户名/姓名"
            clearable
            size="small"
            style="width: 200px"
            @keyup.enter="() => { page = 1; load(); }"
            @clear="() => { page = 1; load(); }"
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
          <el-button size="small" @click="() => { page = 1; load(); }">查询</el-button>
        </div>

        <el-table :data="users" v-loading="loading" size="small" empty-text="没有用户">
          <el-table-column label="用户" min-width="180">
            <template #default="{ row }">
              <div class="u-main">
                <el-avatar :size="28" class="u-avatar">
                  {{ (row.full_name || row.username)[0].toUpperCase() }}
                </el-avatar>
                <div class="u-info">
                  <div class="u-name">
                    {{ row.full_name || row.username }}
                    <el-tag v-if="row.id === userStore.userInfo?.id" size="small" effect="plain">
                      我
                    </el-tag>
                  </div>
                  <div class="u-sub">{{ row.username }}<span v-if="row.email"> · {{ row.email }}</span></div>
                </div>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="角色" width="140">
            <template #default="{ row }">
              <el-select
                :model-value="row.role"
                size="small"
                style="width: 108px"
                @change="(v: string) => changeRole(row, v)"
              >
                <el-option
                  v-for="r in ROLE_OPTIONS"
                  :key="r.value"
                  :label="r.label"
                  :value="r.value"
                />
              </el-select>
            </template>
          </el-table-column>

          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag
                :type="row.is_active ? 'success' : 'info'"
                size="small"
                effect="plain"
              >
                {{ row.is_active ? "正常" : "已停用" }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column label="最后登录" width="160">
            <template #default="{ row }">
              <span class="dim">{{ fmtTime(row.last_login_at) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="创建时间" width="160">
            <template #default="{ row }">
              <span class="dim">{{ fmtTime(row.created_at) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
              <el-button
                link
                :type="row.is_active ? 'warning' : 'success'"
                size="small"
                @click="toggleStatus(row)"
              >
                {{ row.is_active ? "停用" : "启用" }}
              </el-button>
              <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="pager" v-if="total > 20">
          <el-pagination
            v-model:current-page="page"
            :page-size="20"
            :total="total"
            layout="prev, pager, next, total"
            size="small"
            @current-change="load"
          />
        </div>
      </el-card>
    </template>

    <!-- 新建/编辑 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑用户' : '新建用户'"
      width="500px"
    >
      <el-form label-width="88px">
        <el-form-item label="用户名" required>
          <el-input
            v-model="form.username"
            :disabled="!!editing"
            placeholder="登录用，建议用英文"
          />
        </el-form-item>
        <el-form-item v-if="!editing" label="密码" required>
          <el-input v-model="form.password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="form.full_name" placeholder="真实姓名" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="form.email" />
        </el-form-item>
        <el-form-item label="手机">
          <el-input v-model="form.phone" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width: 100%">
            <el-option
              v-for="r in ROLE_OPTIONS"
              :key="r.value"
              :label="r.label"
              :value="r.value"
            >
              <div class="opt">
                <span>{{ r.label }}</span>
                <span class="opt-desc">{{ ROLE_DESC[r.value] }}</span>
              </div>
            </el-option>
          </el-select>
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
.user-page {
  padding: 12px;
}

.mb-12 {
  margin-bottom: 12px;
}

.role-row {
  margin-bottom: 12px;
}

.role-box {
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 11px;
  text-align: center;
  cursor: default;

  .rb-count {
    font-size: 20px;
    font-weight: 500;
    color: var(--el-color-primary);
    font-variant-numeric: tabular-nums;
    line-height: 1.25;
  }

  .rb-label {
    font-size: 11.5px;
    color: var(--el-text-color-secondary);
  }
}

.add-btn {
  width: 100%;
  height: 62px;
}

.perm-card :deep(.el-card__body) {
  padding: 12px 14px;
}

.perm-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 8px 16px;
}

.perm-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.perm-desc {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
}

.table-card :deep(.el-card__body) {
  padding: 12px;
}

.tb-head {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}

.u-main {
  display: flex;
  align-items: center;
  gap: 8px;
}

.u-avatar {
  background: var(--el-color-primary);
  font-size: 12px;
  flex-shrink: 0;
}

.u-info {
  min-width: 0;
}

.u-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-primary);
}

.u-sub {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  margin-top: 1px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dim {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.opt {
  display: flex;
  flex-direction: column;
  line-height: 1.5;
  padding: 2px 0;
}

.opt-desc {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
</style>