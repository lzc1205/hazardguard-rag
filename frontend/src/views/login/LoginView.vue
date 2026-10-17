<template>
  <div class="login-container">
    <el-card class="login-card">
      <h2 class="login-title">HazardGuard</h2>
      <p class="login-subtitle">Dangerous Engineering Compliance Review System</p>
      <el-form ref="formRef" :model="loginForm" :rules="rules" size="large">
        <el-form-item prop="username">
          <el-input
            v-model="loginForm.username"
            placeholder="Username"
            :prefix-icon="User"
          />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            v-model="loginForm.password"
            type="password"
            placeholder="Password"
            :prefix-icon="Lock"
            show-password
            @keyup.enter="handleLogin"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" class="login-btn" @click="handleLogin">
            Login
          </el-button>
        </el-form-item>
        <el-form-item>
          <el-alert type="info" :closable="false" class="demo-hint">
            <template #title>
              演示账号：<code>admin</code> / <code>admin123</code>
              <span class="demo-note">（本地种子数据，公网部署前请务必修改）</span>
            </template>
          </el-alert>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { User, Lock } from "@element-plus/icons-vue";
import type { FormInstance, FormRules } from "element-plus";
import { useUserStore } from "@/stores/userStore";

const router = useRouter();
const userStore = useUserStore();
const formRef = ref<FormInstance>();
const loading = ref(false);

// 不在源码中硬编码任何凭据；演示账号见页面提示与 README
const loginForm = reactive({
  username: "",
  password: "",
});

const rules: FormRules = {
  username: [{ required: true, message: "Please enter username", trigger: "blur" }],
  password: [{ required: true, message: "Please enter password", trigger: "blur" }],
};

async function handleLogin() {
  const valid = await formRef.value?.validate().catch(() => false);
  if (!valid) return;
  loading.value = true;
  try {
    await userStore.login(loginForm.username, loginForm.password);
    router.push("/dashboard");
  } catch {
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped lang="scss">
.login-container {
  width: 100%;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}
.login-card {
  width: 400px;
  padding: 20px;
}
.login-title {
  text-align: center;
  margin: 0 0 8px;
  font-size: 28px;
  color: #303133;
}
.login-subtitle {
  text-align: center;
  margin: 0 0 32px;
  color: #909399;
  font-size: 14px;
}
.login-btn {
  width: 100%;
}
.demo-hint {
  margin-top: 4px;
}
.demo-hint :deep(.el-alert__title) {
  font-size: 12px;
  line-height: 1.6;
}
.demo-hint code {
  padding: 1px 5px;
  border-radius: 3px;
  background: rgba(0, 0, 0, 0.06);
  font-family: "JetBrains Mono", Consolas, monospace;
}
.demo-note {
  opacity: 0.7;
}
</style>
