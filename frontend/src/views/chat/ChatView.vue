<script setup lang="ts">
/**
 * 智能问答页。
 *
 * 展示的不只是答案，还有「Agent 是怎么想、怎么调工具、看到了什么」——
 * ReAct 的推理链折叠展示，让用户能验证答案是否可信。
 *
 * 三种模式对应三种 Agent 编排方式，可现场对比：
 * - ReAct：模型自主决定调哪些工具，适合问法多变
 * - Function Calling：原生 tool_calls，结构化更可靠
 * - Direct：单次检索 + 生成，最快
 */
import { computed, nextTick, ref } from "vue";
import { ElMessage } from "element-plus";
import { chatApi, type AskResult, type ReActStep } from "@/api";

interface Message {
  role: "user" | "assistant";
  content: string;
  result?: AskResult;
}

const messages = ref<Message[]>([]);
const input = ref("");
const mode = ref<"react" | "function_calling" | "direct">("react");
const loading = ref(false);
const scrollRef = ref<HTMLElement>();

const EXAMPLES = [
  "模板支撑立杆的纵横向间距最大不应超过多少？",
  "基坑开挖多深需要组织专家论证？",
  "悬挑式脚手架的锚固长度有什么要求？",
  "食堂饭菜的营养搭配应该怎么设计？",
];

const MODE_LABELS: Record<string, string> = {
  react: "ReAct（自主多轮）",
  function_calling: "Function Calling（原生工具）",
  direct: "Direct（单次检索）",
};

/** 当前正在流式输出的答案 */
const streaming = ref("");

const lastResult = computed(() => {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    if (messages.value[i].result) return messages.value[i].result;
  }
  return null;
});

async function scrollToBottom() {
  await nextTick();
  if (scrollRef.value) {
    scrollRef.value.scrollTop = scrollRef.value.scrollHeight;
  }
}

async function send(text?: string) {
  const q = (text ?? input.value).trim();
  if (!q || loading.value) return;

  messages.value.push({ role: "user", content: q });
  input.value = "";
  loading.value = true;
  streaming.value = "";
  await scrollToBottom();

  try {
    const res = await chatApi.ask(q, mode.value);
    messages.value.push({ role: "assistant", content: "", result: res.data });
    await scrollToBottom();
  } catch (e) {
    ElMessage.error("问答失败，请确认后端与 LLM 服务正常");
  } finally {
    loading.value = false;
  }
}

function toolIcon(action: string): string {
  if (action === "search_regulation") return "Search";
  if (action === "get_clause_detail") return "Document";
  if (action === "list_hazard_categories") return "Grid";
  return "Tools";
}

const ACTION_LABELS: Record<string, string> = {
  search_regulation: "检索法规条文",
  get_clause_detail: "获取条款原文",
  list_hazard_categories: "查询危大类别",
  __parse_error__: "输出格式重试",
};
</script>

<template>
  <div class="chat-page">
    <div ref="scrollRef" class="messages">
      <!-- 欢迎语 -->
      <div v-if="!messages.length" class="welcome">
        <el-icon class="welcome-icon"><ChatDotRound /></el-icon>
        <h2>危大工程法规问答</h2>
        <p class="welcome-sub">
          基于 28 条现行有效法规条文作答，每个结论均标注来源条款；
          知识库未收录时会明确拒答，不编造依据。
        </p>
        <div class="examples">
          <el-tag
            v-for="ex in EXAMPLES"
            :key="ex"
            effect="plain"
            class="example-tag"
            @click="send(ex)"
          >
            {{ ex }}
          </el-tag>
        </div>
      </div>

      <!-- 消息列表 -->
      <div
        v-for="(m, idx) in messages"
        :key="idx"
        class="msg"
        :class="`msg-${m.role}`"
      >
        <div class="bubble">
          <div v-if="m.role === 'user'" class="user-text">{{ m.content }}</div>

          <template v-else-if="m.result">
            <!-- 拒答提示 -->
            <el-alert
              v-if="m.result.refused"
              type="warning"
              :closable="false"
              show-icon
              class="refuse-alert"
              title="系统未出具结论"
            >
              {{ m.result.refuse_reason }}
            </el-alert>

            <!-- 答案（保留换行） -->
            <div class="answer">{{ m.result.answer }}</div>

            <!-- ReAct 推理链 -->
            <el-collapse v-if="m.result.steps?.length" class="steps">
              <el-collapse-item :title="`Agent 推理过程（${m.result.steps.length} 步）`">
                <div v-for="s in m.result.steps" :key="s.step" class="step">
                  <div class="step-head">
                    <span class="step-no">Step {{ s.step }}</span>
                    <el-tag size="small" effect="plain">{{ s.tokens }} tok</el-tag>
                  </div>
                  <div v-if="s.thought" class="step-thought">
                    <span class="step-label">思考</span>
                    {{ s.thought }}
                  </div>
                  <div v-if="s.action" class="step-action">
                    <span class="step-label">行动</span>
                    <el-icon><component :is="toolIcon(s.action)" /></el-icon>
                    <span>{{ ACTION_LABELS[s.action] || s.action }}</span>
                    <code v-if="Object.keys(s.action_input || {}).length" class="args">
                      {{
                        Object.entries(s.action_input)
                          .map(([k, v]) => `${k}: ${v}`)
                          .join(", ")
                      }}
                    </code>
                  </div>
                  <div v-if="s.observation" class="step-obs">
                    <span class="step-label">观察</span>
                    {{ s.observation }}
                  </div>
                </div>
              </el-collapse-item>
            </el-collapse>

            <!-- Function Calling 工具记录 -->
            <div v-if="m.result.tool_calls?.length" class="tool-calls">
              <div class="tc-title">工具调用记录</div>
              <div v-for="(tc, i) in m.result.tool_calls" :key="i" class="tc-item">
                <el-icon><component :is="toolIcon(tc.name)" /></el-icon>
                <span>{{ ACTION_LABELS[tc.name] || tc.name }}</span>
                <code class="args">
                  {{ Object.entries(tc.args || {}).map(([k, v]) => `${k}: ${v}`).join(", ") }}
                </code>
              </div>
            </div>

            <!-- 依据 -->
            <div v-if="m.result.sources?.length" class="sources">
              <div class="src-title">依据来源</div>
              <div v-for="(s, i) in m.result.sources" :key="i" class="src-item">
                <template v-if="s.citation">
                  <el-icon class="src-icon"><Document /></el-icon>
                  <span class="src-cite">{{ s.citation }}</span>
                  <div v-if="s.content" class="src-content">{{ s.content }}</div>
                </template>
                <template v-else-if="s.observation">
                  <el-tag size="small" effect="plain">{{ s.action }}</el-tag>
                  <div class="src-content">{{ s.observation }}</div>
                </template>
                <template v-else-if="s.tool">
                  <el-tag size="small" type="info" effect="plain">{{ s.tool }}</el-tag>
                </template>
              </div>
            </div>

            <!-- 元信息 -->
            <div class="meta">
              <el-tag size="small" effect="plain">{{ MODE_LABELS[m.result.mode] }}</el-tag>
              <span>{{ m.result.tokens }} tokens</span>
              <span v-if="m.result.latency_ms">{{ m.result.latency_ms }}ms</span>
            </div>
          </template>
        </div>
      </div>

      <!-- 加载中 -->
      <div v-if="loading" class="msg msg-assistant">
        <div class="bubble">
          <div class="thinking">
            <el-icon class="is-loading"><Loading /></el-icon>
            <span>{{
              mode === "react"
                ? "Agent 正在推理与调用工具..."
                : mode === "function_calling"
                  ? "正在通过工具查证..."
                  : "正在检索法规..."
            }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="input-bar">
      <div class="mode-switch">
        <el-radio-group v-model="mode" size="small">
          <el-radio-button value="react">ReAct</el-radio-button>
          <el-radio-button value="function_calling">Function Calling</el-radio-button>
          <el-radio-button value="direct">Direct</el-radio-button>
        </el-radio-group>
        <span class="mode-hint">{{ MODE_LABELS[mode] }}</span>
      </div>
      <div class="input-row">
        <el-input
          v-model="input"
          placeholder="输入工程法规问题，支持口语化提问"
          size="large"
          @keyup.enter="send()"
        >
          <template #prefix><el-icon><ChatLineRound /></el-icon></template>
        </el-input>
        <el-button type="primary" size="large" :loading="loading" @click="send()">
          提问
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--el-bg-color-page);
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.welcome {
  text-align: center;
  padding: 50px 20px;

  .welcome-icon {
    font-size: 46px;
    color: var(--el-color-primary);
    opacity: 0.75;
  }

  h2 {
    margin: 14px 0 6px;
    font-size: 19px;
    font-weight: 500;
  }

  .welcome-sub {
    margin: 0 auto 20px;
    max-width: 520px;
    font-size: 13px;
    color: var(--el-text-color-secondary);
    line-height: 1.7;
  }

  .examples {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: center;
    max-width: 640px;
    margin: 0 auto;
  }

  .example-tag {
    cursor: pointer;

    &:hover {
      color: var(--el-color-primary);
      border-color: var(--el-color-primary);
    }
  }
}

.msg {
  display: flex;
  margin-bottom: 16px;

  &.msg-user {
    justify-content: flex-end;
  }
}

.bubble {
  max-width: 78%;
  padding: 12px 15px;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.75;
}

.msg-user .bubble {
  background: var(--el-color-primary);
  color: #fff;
}

.msg-assistant .bubble {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
}

.user-text {
  white-space: pre-wrap;
  word-break: break-word;
}

.answer {
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--el-text-color-primary);
}

.refuse-alert {
  margin-bottom: 10px;
}

.steps {
  margin-top: 12px;
  border-top: 1px dashed var(--el-border-color-lighter);
  padding-top: 6px;
}

.step {
  padding: 8px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);

  &:last-child {
    border-bottom: none;
  }

  .step-head {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 5px;
  }

  .step-no {
    font-size: 12px;
    font-weight: 500;
    color: var(--el-color-primary);
  }

  .step-label {
    display: inline-block;
    width: 30px;
    font-size: 11.5px;
    color: var(--el-text-color-placeholder);
    flex-shrink: 0;
  }

  .step-thought,
  .step-action,
  .step-obs {
    font-size: 12.5px;
    line-height: 1.6;
    color: var(--el-text-color-regular);
    margin-bottom: 3px;
    word-break: break-word;
  }

  .step-action {
    display: flex;
    align-items: center;
    gap: 5px;
    flex-wrap: wrap;
  }

  .args {
    font-size: 11px;
    color: var(--el-text-color-secondary);
    background: var(--el-fill-color-light);
    padding: 1px 5px;
    border-radius: 3px;
  }
}

.tool-calls {
  margin-top: 10px;
  padding: 8px 10px;
  background: var(--el-fill-color-light);
  border-radius: 5px;

  .tc-title {
    font-size: 11.5px;
    color: var(--el-text-color-secondary);
    margin-bottom: 5px;
  }

  .tc-item {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12.5px;
    margin-bottom: 4px;
    flex-wrap: wrap;
  }
}

.sources {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--el-border-color-lighter);

  .src-title {
    font-size: 11.5px;
    color: var(--el-text-color-secondary);
    margin-bottom: 6px;
  }

  .src-item {
    display: flex;
    align-items: flex-start;
    gap: 6px;
    font-size: 12.5px;
    margin-bottom: 7px;
    flex-wrap: wrap;
  }

  .src-icon {
    color: var(--el-color-success);
    margin-top: 3px;
    flex-shrink: 0;
  }

  .src-cite {
    color: var(--el-color-primary);
    font-weight: 500;
  }

  .src-content {
    width: 100%;
    color: var(--el-text-color-secondary);
    line-height: 1.6;
    padding-left: 20px;
    word-break: break-word;
  }
}

.meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--el-border-color-lighter);
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
}

.thinking {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.input-bar {
  border-top: 1px solid var(--el-border-color-lighter);
  padding: 12px 20px 16px;
  background: var(--el-bg-color);
}

.mode-switch {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;

  .mode-hint {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }
}

.input-row {
  display: flex;
  gap: 10px;
  max-width: 900px;
  margin: 0 auto;
}
</style>