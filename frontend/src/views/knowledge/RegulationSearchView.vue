<script setup lang="ts">
/**
 * 法规速查（检索页）。
 *
 * 把 RAG 检索层的能力直接暴露给用户：混合检索 + 强制性条文标记 + 溯源。
 * 面试时可现场演示「口语化提问也能命中精确条款」。
 */
import { computed, onMounted, ref } from "vue";
import { knowledgeApi, searchApi } from "@/api";
import { categoryName, type SearchHit } from "@/utils/constants";

const query = ref("模板支撑立杆的纵横向间距最大不应超过多少");
const topK = ref(8);
const onlyMandatory = ref(false);
const category = ref<string>("");
const loading = ref(false);
const results = ref<SearchHit[]>([]);
const searched = ref(false);
const categories = ref<Array<{ key: string; name: string; hazard_threshold: string }>>([]);

async function doSearch() {
  if (!query.value.trim()) return;
  loading.value = true;
  try {
    const res = await searchApi.search({
      query: query.value,
      top_k: topK.value,
      category: category.value || undefined,
      only_mandatory: onlyMandatory.value,
    });
    results.value = res.data.items;
    searched.value = true;
  } catch {
    results.value = [];
  } finally {
    loading.value = false;
  }
}

const mandatoryCount = computed(() => results.value.filter((r) => r.is_mandatory).length);

onMounted(async () => {
  try {
    const res = await knowledgeApi.categories();
    categories.value = res.data;
  } catch {
    categories.value = [];
  }
});

const EXAMPLES = [
  "深基坑多少米需要专家论证",
  "悬挑脚手架锚固长度要求",
  "起重吊装作业前需要检查什么",
  "脚手架连墙件间距怎么规定",
];
</script>

<template>
  <div class="regulation-search">
    <el-card shadow="never" class="search-card">
      <div class="search-bar">
        <el-input
          v-model="query"
          placeholder="输入问题或条款关键词，支持口语化提问"
          size="large"
          clearable
          @keyup.enter="doSearch"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button type="primary" size="large" :loading="loading" @click="doSearch">
          检索
        </el-button>
      </div>

      <div class="options">
        <el-select v-model="category" placeholder="全部类别" clearable size="small" style="width: 200px">
          <el-option
            v-for="c in categories"
            :key="c.key"
            :label="c.name"
            :value="c.key"
          />
        </el-select>
        <el-switch v-model="onlyMandatory" active-text="只看强制性条文" size="small" />
        <el-input-number v-model="topK" :min="1" :max="20" size="small" controls-position="right" />
        <span class="opt-label">返回条数</span>
      </div>

      <div class="examples">
        <span class="ex-label">试试：</span>
        <el-tag
          v-for="ex in EXAMPLES"
          :key="ex"
          size="small"
          effect="plain"
          class="ex-tag"
          @click="query = ex; doSearch()"
        >
          {{ ex }}
        </el-tag>
      </div>
    </el-card>

    <div v-if="searched" class="result-meta">
      召回 <strong>{{ results.length }}</strong> 条，其中强制性条文
      <strong>{{ mandatoryCount }}</strong> 条
      <span class="hint">（BM25 关键词路 + 向量语义路 RRF 融合）</span>
    </div>

    <div class="result-list">
      <el-card
        v-for="(r, i) in results"
        :key="i"
        shadow="never"
        class="result-item"
        :class="{ 'is-mandatory': r.is_mandatory }"
      >
        <div class="result-head">
          <el-tag size="small" effect="dark">{{ r.regulation_code }}</el-tag>
          <span class="clause">{{ r.clause_no }}</span>
          <el-tag v-if="r.is_mandatory" size="small" type="danger" effect="dark">强制性</el-tag>
          <el-tag size="small" effect="plain">{{ categoryName(r.category || "") }}</el-tag>
          <span class="grow" />
          <span v-if="r.page_number" class="page">第 {{ r.page_number }} 页</span>
          <span class="score">{{ r.score.toFixed(4) }}</span>
        </div>
        <div class="citation">{{ r.citation }}</div>
        <p class="content">{{ r.content }}</p>
      </el-card>

      <el-empty v-if="searched && !results.length" description="未检索到相关条款" />
      <div v-if="!searched" class="placeholder">
        <el-empty description="输入问题开始检索，支持口语化提问" />
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.regulation-search {
  padding: 16px;
  max-width: 1000px;
  margin: 0 auto;
}

.search-card :deep(.el-card__body) {
  padding: 16px;
}

.search-bar {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}

.options {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;

  .opt-label {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }
}

.examples {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;

  .ex-label {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  .ex-tag {
    cursor: pointer;

    &:hover {
      color: var(--el-color-primary);
      border-color: var(--el-color-primary);
    }
  }
}

.result-meta {
  margin: 14px 0 10px;
  font-size: 13px;
  color: var(--el-text-color-regular);

  .hint {
    color: var(--el-text-color-secondary);
    font-size: 12px;
    margin-left: 4px;
  }
}

.result-item {
  margin-bottom: 10px;
  border-left: 3px solid var(--el-border-color);

  &.is-mandatory {
    border-left-color: var(--el-color-danger);
  }

  :deep(.el-card__body) {
    padding: 12px 14px;
  }

  .result-head {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;

    .clause {
      font-size: 13px;
      font-weight: 500;
      color: var(--el-text-color-primary);
    }

    .grow {
      flex: 1;
    }

    .page,
    .score {
      font-size: 11.5px;
      color: var(--el-text-color-secondary);
      font-variant-numeric: tabular-nums;
    }
  }

  .citation {
    margin-top: 6px;
    font-size: 12px;
    color: var(--el-color-primary);
  }

  .content {
    margin: 8px 0 0;
    font-size: 13px;
    line-height: 1.7;
    color: var(--el-text-color-regular);
  }
}

.placeholder {
  padding-top: 40px;
}
</style>