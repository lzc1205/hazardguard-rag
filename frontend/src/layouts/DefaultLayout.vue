<template>
  <el-container class="default-layout">
    <el-aside width="220px" class="sidebar">
      <div class="logo">
        <div class="logo-icon">
          <!-- 描边盾牌作为底衬，内嵌「危」字：比纯图标更能表明这是合规审查系统 -->
          <el-icon><Shield /></el-icon>
          <span class="logo-char">危</span>
        </div>
        <div class="logo-text">
          <h2>HazardGuard</h2>
          <span>危大工程合规审查</span>
        </div>
      </div>

      <el-menu
        :default-active="activeMenu"
        router
        class="nav-menu"
      >
        <el-menu-item index="/dashboard">
          <el-icon><Odometer /></el-icon>
          <span>概览</span>
        </el-menu-item>
        <el-menu-item index="/projects">
          <el-icon><OfficeBuilding /></el-icon>
          <span>项目管理</span>
        </el-menu-item>
        <el-menu-item index="/documents">
          <el-icon><Document /></el-icon>
          <span>文档管理</span>
        </el-menu-item>
        <el-menu-item index="/review">
          <el-icon><Checked /></el-icon>
          <span>审查工作台</span>
        </el-menu-item>
        <el-menu-item index="/reviews">
          <el-icon><Tickets /></el-icon>
          <span>审查记录</span>
        </el-menu-item>
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>智能问答</span>
        </el-menu-item>
        <el-menu-item index="/regulations">
          <el-icon><Search /></el-icon>
          <span>法规速查</span>
        </el-menu-item>
        <el-menu-item index="/knowledge">
          <el-icon><Collection /></el-icon>
          <span>知识库管理</span>
        </el-menu-item>
        <el-menu-item index="/stats">
          <el-icon><TrendCharts /></el-icon>
          <span>统计分析</span>
        </el-menu-item>

        <div class="nav-divider" />

        <el-menu-item index="/trace">
          <el-icon><Operation /></el-icon>
          <span>Agent 编排记录</span>
        </el-menu-item>
        <el-menu-item index="/prompts">
          <el-icon><Document /></el-icon>
          <span>Prompt 管理</span>
        </el-menu-item>
        <el-menu-item v-if="isAdmin" index="/users">
          <el-icon><User /></el-icon>
          <span>用户管理</span>
        </el-menu-item>
        <el-menu-item v-if="isAdmin" index="/settings">
          <el-icon><Setting /></el-icon>
          <span>系统设置</span>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-footer">
        <div class="tech-tag">RAG · 多智能体</div>
      </div>
    </el-aside>

    <el-container>
      <el-header class="topbar">
        <div class="breadcrumb">
          <span class="page-title">{{ pageTitle }}</span>
        </div>
        <div class="topbar-right">
          <el-tag v-if="userStore.userInfo?.role" size="small" effect="plain" class="role-tag">
            {{ roleLabel }}
          </el-tag>
          <el-dropdown trigger="click">
            <span class="user-info">
              <el-avatar :size="26" class="avatar">
                {{ (userStore.userInfo?.username || "U")[0].toUpperCase() }}
              </el-avatar>
              <span class="uname">{{ userStore.userInfo?.username || "用户" }}</span>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="handleLogout">
                  <el-icon><SwitchButton /></el-icon>
                  退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useUserStore } from "@/stores/userStore";
import { ElMessage } from "element-plus";

const route = useRoute();
const router = useRouter();
const userStore = useUserStore();

const activeMenu = computed(() => route.path);

const pageTitle = computed(() => (route.meta.title as string) || "概览");

const ROLE_LABELS: Record<string, string> = {
  admin: "管理员",
  supervisor: "总监",
  reviewer: "审查员",
  technician: "技术员",
  viewer: "访客",
};

const roleLabel = computed(
  () => ROLE_LABELS[userStore.userInfo?.role || ""] || "访客",
);

/** 管理员专属菜单（用户管理 / 系统设置） */
const isAdmin = computed(() => userStore.userInfo?.role === "admin");

function handleLogout() {
  userStore.logout();
  ElMessage.success("已退出登录");
  router.push("/login");
}
</script>

<style scoped lang="scss">
.default-layout {
  height: 100vh;
}

.sidebar {
  background: #0f1c2e;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-right: 1px solid rgba(255, 255, 255, 0.04);
}

.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);

  /* 原来是一整块实心蓝方块，在深色侧栏上过于抢眼。
     改为「描边盾牌 + 危字」，去掉大面积色块但保留识别度。 */
  .logo-icon {
    width: 30px;
    height: 34px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    color: #4a9eff;
    position: relative;
    filter: drop-shadow(0 0 6px rgba(74, 158, 255, 0.35));

    svg {
      position: absolute;
      inset: 0;
      margin: auto;
      width: 30px;
      height: 30px;
      fill: none;
      stroke: currentColor;
      stroke-width: 1.4;
    }
  }

  .logo-char {
    position: relative;
    z-index: 1;
    font-size: 15px;
    font-weight: 700;
    color: #fff;
    line-height: 1;
    transform: translateY(-1px);
  }

  .logo-text {
    min-width: 0;

    h2 {
      color: #fff;
      font-size: 14.5px;
      margin: 0;
      line-height: 1.35;
      font-weight: 600;
      letter-spacing: 0.2px;
    }
    span {
      font-size: 11px;
      color: rgba(255, 255, 255, 0.42);
      line-height: 1.3;
    }
  }
}

/* ---------------------------------------------------------------
   菜单：不用整块主色填充。
   整块高亮在深色侧栏上是一整块蓝色，视觉过重；
   改为「左侧 3px 指示条 + 淡色底 + 白字」，层级更清晰。
   --------------------------------------------------------------- */
.nav-menu {
  border-right: none;
  flex: 1;
  padding: 10px 8px 0;
  background: transparent;
  overflow-y: auto;

  :deep(.el-menu-item) {
    height: 40px;
    line-height: 40px;
    margin-bottom: 3px;
    padding-left: 14px !important;
    border-radius: 7px;
    color: rgba(255, 255, 255, 0.68);
    font-size: 13.5px;
    position: relative;
    transition: background 0.18s, color 0.18s;

    .el-icon {
      color: rgba(255, 255, 255, 0.45);
      font-size: 16px;
      transition: color 0.18s;
    }

    &:hover {
      background: rgba(255, 255, 255, 0.06);
      color: rgba(255, 255, 255, 0.92);
    }

    &.is-active {
      background: rgba(74, 158, 255, 0.14);
      color: #fff;
      font-weight: 500;

      /* 左侧指示条：比整块填充轻，但指向性更强 */
      &::before {
        content: "";
        position: absolute;
        left: 0;
        top: 50%;
        transform: translateY(-50%);
        width: 3px;
        height: 18px;
        border-radius: 0 3px 3px 0;
        background: #4a9eff;
      }

      .el-icon {
        color: #4a9eff;
      }
    }
  }
}

/* 菜单分组分隔线：区分「业务功能」与「运维配置」 */
.nav-divider {
  height: 1px;
  background: rgba(255, 255, 255, 0.08);
  margin: 10px 12px;
}

.sidebar-footer {
  padding: 10px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);

  .tech-tag {
    display: block;
    text-align: center;
    font-size: 10.5px;
    color: rgba(255, 255, 255, 0.32);
    padding: 4px 0;
    letter-spacing: 0.3px;
  }
}

.topbar {
  background: #fff;
  border-bottom: 1px solid #e8e8e8;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  height: 52px;

  .page-title {
    font-size: 14.5px;
    font-weight: 500;
    color: var(--el-text-color-primary);
  }

  .topbar-right {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .role-tag {
    opacity: 0.85;
  }

  .user-info {
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .avatar {
    background: var(--el-color-primary);
    font-size: 12px;
  }

  .uname {
    font-size: 13px;
  }
}

.main-content {
  background: #f0f2f5;
  padding: 0;
  overflow-y: auto;
}
</style>