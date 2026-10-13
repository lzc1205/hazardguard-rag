<script setup lang="ts">
/**
 * 单条审查意见卡片。
 *
 * 视觉上必须让「拒答」和「有依据的结论」明显区分开——
 * 拒答项要有醒目的标识，因为它是系统诚实性的体现，不是缺陷。
 */
import { computed } from "vue";
import {
  COMPLIANCE_STATUS_META,
  SEVERITY_META,
  REVIEW_DIMENSION_LABELS,
  categoryMeta,
} from "@/utils/constants";
import type { ReviewItem } from "@/utils/constants";

const props = defineProps<{
  item: ReviewItem;
  index?: number;
}>();

const statusMeta = computed(
  () => COMPLIANCE_STATUS_META[props.item.compliance_status] || COMPLIANCE_STATUS_META.needs_confirmation,
);

const severityMeta = computed(() =>
  props.item.severity ? SEVERITY_META[props.item.severity] : null,
);

const dimensionLabel = computed(
  () => REVIEW_DIMENSION_LABELS[props.item.category] || props.item.category,
);

/** 是否为拒答项——无依据且被证据闸门拦截。 */
const isRefused = computed(() => props.item.refused === true);

const cat = computed(() => categoryMeta(props.item.category));
</script>

<template>
  <el-card
    class="review-item"
    :class="{ 'is-refused': isRefused, 'is-non-compliant': item.compliance_status === 'non_compliant' }"
    shadow="never"
  >
    <template #header>
      <div class="item-header">
        <span class="seq">{{ (index ?? 0) + 1 }}</span>
        <el-tag :type="statusMeta.type" size="small" effect="dark">
          {{ statusMeta.label }}
        </el-tag>
        <span v-if="severityMeta" :class="['severity', `sev-${item.severity}`]">
          {{ severityMeta.label }}
        </span>
        <el-tag size="small" effect="plain" class="dimension">
          {{ dimensionLabel }}
        </el-tag>
        <span class="grow" />
        <el-tag v-if="isRefused" size="small" type="warning" effect="plain">
          <el-icon><Warning /></el-icon>
          证据不足·已拒答
        </el-tag>
      </div>
      <div class="item-title">{{ item.item_title }}</div>
    </template>

    <!-- 方案内容摘录 -->
    <div v-if="item.scheme_content" class="block">
      <div class="block-label">方案摘录</div>
      <div class="scheme-content">{{ item.scheme_content }}</div>
    </div>

    <!-- 审查意见 -->
    <div class="block">
      <div class="block-label">审查意见</div>
      <p class="comment">{{ item.review_comment }}</p>
    </div>

    <!-- 法规依据：拒答项必须明确说明为什么没有依据 -->
    <div class="block">
      <div class="block-label">
        法规依据
        <el-tag v-if="item.citation_verified" size="small" type="success" effect="plain">
          已溯源
        </el-tag>
        <el-tag v-else size="small" type="info" effect="plain">无依据</el-tag>
      </div>

      <div v-if="item.regulation_reference" class="citation">
        <el-icon class="cite-icon"><Document /></el-icon>
        <span>{{ item.regulation_reference }}</span>
      </div>

      <div v-else-if="isRefused" class="refuse-box">
        <el-icon><InfoFilled /></el-icon>
        <div>
          <div class="refuse-title">系统未出具判定</div>
          <div class="refuse-reason">
            {{ item.refuse_reason || "知识库中未找到对应的现行有效条款依据，按证据闸门拒绝出具结论" }}
          </div>
        </div>
      </div>

      <div v-else class="no-citation">
        未检索到直接对应的条款依据
      </div>
    </div>

    <!-- 修改建议 -->
    <div v-if="item.modification_suggestion" class="block">
      <div class="block-label">修改建议</div>
      <p class="suggestion">{{ item.modification_suggestion }}</p>
    </div>

    <!-- 产出该意见的 Agent -->
    <div v-if="item.agent_source" class="agent-source">
      <el-icon><Cpu /></el-icon>
      {{ item.agent_source }}
    </div>
  </el-card>
</template>

<style scoped lang="scss">
.review-item {
  margin-bottom: 12px;
  border-left: 3px solid var(--el-border-color);

  &.is-refused {
    border-left-color: var(--el-color-warning);
    background: var(--el-color-warning-light-9);
  }

  &.is-non-compliant {
    border-left-color: var(--el-color-danger);
  }

  :deep(.el-card__header) {
    padding: 12px 16px;
  }

  .item-header {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;

    .seq {
      width: 20px;
      height: 20px;
      border-radius: 50%;
      background: var(--el-color-primary);
      color: #fff;
      font-size: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }

    .severity {
      font-size: 12px;
      padding: 1px 6px;
      border-radius: 3px;

      &.sev-critical {
        color: #fff;
        background: var(--el-color-danger);
      }
      &.sev-major {
        color: #fff;
        background: var(--el-color-warning);
      }
      &.sev-minor {
        color: #fff;
        background: var(--el-color-info);
      }
      &.sev-suggestion {
        color: #fff;
        background: var(--el-color-primary);
      }
    }

    .dimension {
      opacity: 0.85;
    }

    .grow {
      flex: 1;
    }
  }

  .item-title {
    margin-top: 8px;
    font-size: 14px;
    font-weight: 500;
    color: var(--el-text-color-primary);
  }

  .block {
    margin-top: 12px;

    .block-label {
      font-size: 12px;
      color: var(--el-text-color-secondary);
      margin-bottom: 4px;
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .scheme-content {
      font-size: 13px;
      color: var(--el-text-color-regular);
      background: var(--el-fill-color-light);
      padding: 8px 10px;
      border-radius: 4px;
      line-height: 1.6;
    }

    .comment,
    .suggestion {
      margin: 0;
      font-size: 13px;
      line-height: 1.7;
      color: var(--el-text-color-regular);
    }

    .suggestion {
      color: var(--el-color-primary);
    }

    .citation {
      display: flex;
      align-items: flex-start;
      gap: 6px;
      font-size: 12.5px;
      line-height: 1.6;
      color: var(--el-text-color-regular);
      background: var(--el-color-success-light-9);
      padding: 8px 10px;
      border-radius: 4px;
      border-left: 2px solid var(--el-color-success);

      .cite-icon {
        color: var(--el-color-success);
        margin-top: 2px;
        flex-shrink: 0;
      }
    }

    .refuse-box {
      display: flex;
      gap: 8px;
      padding: 10px 12px;
      background: var(--el-fill-color-light);
      border-radius: 4px;
      border: 1px dashed var(--el-color-warning);
      font-size: 12.5px;
      line-height: 1.6;

      .el-icon {
        color: var(--el-color-warning);
        margin-top: 2px;
        flex-shrink: 0;
      }

      .refuse-title {
        font-weight: 500;
        color: var(--el-text-color-primary);
        margin-bottom: 2px;
      }

      .refuse-reason {
        color: var(--el-text-color-secondary);
      }
    }

    .no-citation {
      font-size: 12.5px;
      color: var(--el-text-color-secondary);
      padding: 8px 10px;
      background: var(--el-fill-color-lighter);
      border-radius: 4px;
    }
  }

  .agent-source {
    margin-top: 12px;
    padding-top: 8px;
    border-top: 1px dashed var(--el-border-color-lighter);
    font-size: 11.5px;
    color: var(--el-text-color-placeholder);
    display: flex;
    align-items: center;
    gap: 4px;
  }
}
</style>