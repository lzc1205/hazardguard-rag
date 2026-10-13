<script setup lang="ts">
/**
 * 系统设置（需求文档 7.4.18，admin 专属）。
 *
 * **刻意不做「可编辑的连接字符串」**：
 * 让人在页面上改数据库地址、API Key 是危险设计——
 * 改错了整个系统起不来，而且这类设置应该走部署配置文件。
 * 这里只做「查看运行状态 + 危险操作确认」。
 */
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { vectorApi, knowledgeApi, promptApi } from "@/api";
import { useUserStore } from "@/stores/userStore";

const userStore = useUserStore();
const loading = ref(false);
const busy = ref("");

const llm = ref<any>(null);
const vector = ref<any>(null);
const prompts = ref<any[]>([]);
const versions = ref<any[]>([]);

const isAdmin = computed(() => userStore.userInfo?.role === "admin");

/** 批量自检：逐项调只读接口，任何一项失败都标红 */
const checks = ref<Array<{ name: string; status: string; detail: string }>>([]);

async function runChecks() {
  loading.value = true;
  const list: Array<{ name: string; status: string; detail: string }> = [];
  try {
    const [v, l, p, vv, cats] = await Promise.all([
      vectorApi.stats().catch(() => null),
      promptApi.llmStatus().catch(() => null),
      promptApi.list().catch(() => null),
      promptApi.versions().catch(() => null),
      knowledgeApi.categories().catch(() => null),
    ]);
    vector.value = v?.data;
    llm.value = l?.data;
    prompts.value = p?.data || [];
    versions.value = vv?.data?.items || [];

    list.push({
      name: "数据库连接",
      status: "ok",
      detail: "PostgreSQL 已连接（审查/文档/知识库可正常读写）",
    });
    list.push({
      name: "检索索引",
      status:
        vector.value && vector.value.coverage > 0 ? "ok" : "warn",
      detail: `向量覆盖 ${vector.value?.coverage ?? 0}%（${vector.value?.indexed_chunks ?? 0}/${vector.value?.total_chunks ?? 0} 分块，${vector.value?.dimension ?? "-"} 维）`,
    });
    list.push({
      name: "大模型",
      status: llm.value?.available ? "ok" : "warn",
      detail: llm.value?.available
        ? `${llm.value.model}　说理由 LLM 生成`
        : "未配置 API Key，当前为规则模式（结论不受影响，仅说理用模板）",
    });
    list.push({
      name: "Prompt 模板",
      status: prompts.value.length ? "ok" : "warn",
      detail: `${prompts.value.length} 个模板 / ${versions.value.length} 条版本记录`,
    });
    list.push({
      name: "危大类别",
      status: cats?.data?.length ? "ok" : "warn",
      detail: `已加载 ${cats?.data?.length ?? 0} 个类别及判定阈值`,
    });
    checks.value = list;
  } catch (err) {
    ElMessage.error("自检失败");
  } finally {
    loading.value = false;
  }
}

async function doSyncPrompts() {
  busy.value = "sync";
  try {
    const res = await promptApi.sync(true);
    const d = (res as any).data || {};
    ElMessage.success(`已同步：新增 ${d.created}，更新 ${d.updated}`);
    runChecks();
  } catch {
    ElMessage.error("同步失败");
  } finally {
    busy.value = "";
  }
}

async function doReindex() {
  try {
    await ElMessageBox.confirm(
      "重建向量索引会重新计算全部法规条目的向量。数据量较大时可能耗时数分钟，期间检索会变慢。确认执行？",
      "重建向量索引",
      { type: "warning", confirmButtonText: "开始重建", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  busy.value = "reindex";
  try {
    const res = await vectorApi.reindex();
    const d = (res as any).data || {};
    ElMessage.success(`重建完成：${d.chunks_indexed ?? 0} 个分块`);
    runChecks();
  } catch {
    ElMessage.error("重建失败");
  } finally {
    busy.value = "";
  }
}

function statusMeta(s: string) {
  return (
    {
      ok: { label: "正常", type: "success" },
      warn: { label: "注意", type: "warning" },
      err: { label: "异常", type: "danger" },
    }[s] || { label: s, type: "info" }
  );
}

onMounted(runChecks);
</script>

<template>
  <div class="settings-page">
    <el-alert
      v-if="!isAdmin"
      type="warning"
      :closable="false"
      show-icon
      title="需要管理员权限"
      description="当前账号无权访问系统设置。"
      class="mb-12"
    />

    <template v-else>
      <!-- 运行状态自检 -->
      <el-card shadow="never" class="panel mb-12">
        <template #header>
          <div class="panel-head">
            <span>运行状态自检</span>
            <el-button
              size="small"
              :loading="loading"
              @click="runChecks"
            >
              <el-icon><Refresh /></el-icon>重新检测
            </el-button>
          </div>
        </template>

        <div v-loading="loading" class="checks">
          <div v-for="c in checks" :key="c.name" class="check-row">
            <el-tag :type="statusMeta(c.status).type as any" size="small" effect="plain">
              {{ statusMeta(c.status).label }}
            </el-tag>
            <span class="ck-name">{{ c.name }}</span>
            <span class="ck-detail">{{ c.detail }}</span>
          </div>
        </div>
      </el-card>

      <el-row :gutter="12">
        <!-- LLM 配置 -->
        <el-col :span="12">
          <el-card shadow="never" class="panel">
            <template #header><span>大模型配置</span></template>
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item label="运行模式">
                <el-tag :type="llm?.available ? 'success' : 'info'" size="small" effect="plain">
                  {{ llm?.available ? "LLM 模式" : "规则模式" }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="模型">
                {{ llm?.model || "-" }}
              </el-descriptions-item>
              <el-descriptions-item label="接口地址">
                <code class="code">{{ llm?.base_url || "-" }}</code>
              </el-descriptions-item>
              <el-descriptions-item label="说明">
                {{ llm?.note || "-" }}
              </el-descriptions-item>
            </el-descriptions>

            <el-alert type="info" :closable="false" class="mt-10">
              <template #title>
                API Key 通过环境变量 <code>LLM_API_KEY</code> 配置，不在页面保存。
              </template>
              <div class="alert-note">
                这样做的原因：Key 一旦存进数据库，备份、日志、导出都可能泄露。
                环境变量只在进程启动时读取，泄露面最小。
              </div>
            </el-alert>
          </el-card>
        </el-col>

        <!-- 检索配置 -->
        <el-col :span="12">
          <el-card shadow="never" class="panel">
            <template #header><span>检索与索引</span></template>
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item label="检索方式">
                BM25 + 字符向量 + PGVector 三路融合
              </el-descriptions-item>
              <el-descriptions-item label="分块总数">
                {{ vector?.total_chunks ?? "-" }}
              </el-descriptions-item>
              <el-descriptions-item label="已向量化">
                {{ vector?.indexed_chunks ?? "-" }}
              </el-descriptions-item>
              <el-descriptions-item label="向量维度">
                {{ vector?.dimension ?? "-" }}
              </el-descriptions-item>
              <el-descriptions-item label="Embedding 模型">
                <code class="code">
                  {{ Object.keys(vector?.models || {}).join(", ") || "-" }}
                </code>
              </el-descriptions-item>
            </el-descriptions>
          </el-card>
        </el-col>
      </el-row>

      <!-- 维护操作 -->
      <el-card shadow="never" class="panel mt-12">
        <template #header><span>维护操作</span></template>

        <div class="actions">
          <div class="action">
            <div class="a-head">
              <span class="a-title">同步 Prompt 到数据库</span>
              <el-tag size="small" type="info" effect="plain">改了模板后用</el-tag>
            </div>
            <p class="a-desc">
              把代码中的 Prompt 模板写入 prompt_versions 表，形成版本记录。
              这样「改了一版 Prompt」才有据可查、可回溯。
            </p>
            <el-button
              size="small"
              :loading="busy === 'sync'"
              @click="doSyncPrompts"
            >
              <el-icon><Upload /></el-icon>立即同步
            </el-button>
          </div>

          <div class="action">
            <div class="a-head">
              <span class="a-title">重建向量索引</span>
              <el-tag size="small" type="warning" effect="plain">耗时操作</el-tag>
            </div>
            <p class="a-desc">
              换 Embedding 模型或知识库大量增删后必须执行，否则新旧向量混在一起，
              检索结果会失真。
            </p>
            <el-button
              size="small"
              type="warning"
              :loading="busy === 'reindex'"
              @click="doReindex"
            >
              <el-icon><Refresh /></el-icon>开始重建
            </el-button>
          </div>
        </div>
      </el-card>

      <!-- 设计说明 -->
      <el-card shadow="never" class="panel mt-12">
        <template #header><span>为什么这里不能改配置</span></template>
        <div class="why">
          <p>
            <b>数据库连接、API Key、上传目录</b>这类设置刻意做成只读。
          </p>
          <p>
            原因有三点：
          </p>
          <ul>
            <li><b>改错代价高</b>——页面填错数据库地址，整个系统直接起不来，且页面本身也访问不到了，无法撤销</li>
            <li><b>这类设置属于部署配置</b>——应该在 <code>.env</code> / 容器环境变量里，改完重启生效，而不是每次点页面</li>
            <li><b>安全</b>——API Key 存进数据库就可能被备份、日志、导出带出去；环境变量的泄露面最小</li>
          </ul>
          <p class="why-foot">
            需要改这些参数时，编辑 <code>backend/.env</code> 后重启服务即可。
          </p>
        </div>
      </el-card>
    </template>
  </div>
</template>

<style scoped lang="scss">
.settings-page {
  padding: 12px;
}

.mb-12 {
  margin-bottom: 12px;
}

.mt-12 {
  margin-top: 12px;
}

.mt-10 {
  margin-top: 10px;
}

.panel :deep(.el-card__body) {
  padding: 14px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

/* 自检列表 */
.checks {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.check-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 9px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);

  &:last-child {
    border-bottom: none;
  }
}

.ck-name {
  width: 108px;
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  flex-shrink: 0;
}

.ck-detail {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}

.code {
  font-family: Consolas, monospace;
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
}

.alert-note {
  font-size: 11.5px;
  line-height: 1.75;
  color: var(--el-text-color-regular);
  margin-top: 3px;
}

/* 维护操作 */
.actions {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 20px;
}

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

/* 设计说明 */
.why {
  font-size: 12.5px;
  line-height: 1.9;
  color: var(--el-text-color-regular);

  p {
    margin: 0 0 8px;
  }

  ul {
    margin: 0 0 8px;
    padding-left: 20px;
  }

  li {
    margin-bottom: 4px;
  }

  b {
    font-weight: 500;
    color: var(--el-text-color-primary);
  }

  code {
    font-family: Consolas, monospace;
    font-size: 11.5px;
    background: var(--el-fill-color-light);
    padding: 1px 5px;
    border-radius: 3px;
  }

  .why-foot {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--el-border-color-lighter);
    color: var(--el-text-color-secondary);
  }
}
</style>