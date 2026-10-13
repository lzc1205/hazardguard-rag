import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";

const routes: RouteRecordRaw[] = [
  {
    path: "/login",
    name: "Login",
    component: () => import("@/views/login/LoginView.vue"),
    meta: { title: "登录", layout: "blank" },
  },
  {
    path: "/",
    redirect: "/dashboard",
    component: () => import("@/layouts/DefaultLayout.vue"),
    children: [
      {
        path: "dashboard",
        name: "Dashboard",
        component: () => import("@/views/dashboard/DashboardView.vue"),
        meta: { title: "概览", requiresAuth: true },
      },
      {
        path: "review",
        name: "Review",
        component: () => import("@/views/review/ReviewWorkbenchView.vue"),
        meta: { title: "审查工作台", requiresAuth: true },
      },
      {
        path: "reviews",
        name: "ReviewList",
        component: () => import("@/views/review/ReviewListView.vue"),
        meta: { title: "审查记录", requiresAuth: true },
      },
      {
        path: "reports/:id",
        name: "ReportPreview",
        component: () => import("@/views/report/ReportPreviewView.vue"),
        meta: { title: "审查报告", requiresAuth: true },
      },
      {
        path: "projects",
        name: "Projects",
        component: () => import("@/views/project/ProjectListView.vue"),
        meta: { title: "项目管理", requiresAuth: true },
      },
      {
        path: "projects/:id",
        name: "ProjectDetail",
        component: () => import("@/views/project/ProjectDetailView.vue"),
        meta: { title: "项目详情", requiresAuth: true },
      },
      {
        path: "documents",
        name: "Documents",
        component: () => import("@/views/document/DocumentListView.vue"),
        meta: { title: "文档管理", requiresAuth: true },
      },
      {
        path: "documents/:id",
        name: "DocumentDetail",
        component: () => import("@/views/document/DocumentDetailView.vue"),
        meta: { title: "文档详情", requiresAuth: true },
      },
      {
        path: "regulations",
        name: "Regulations",
        component: () => import("@/views/knowledge/RegulationSearchView.vue"),
        meta: { title: "法规速查", requiresAuth: true },
      },
      {
        path: "chat",
        name: "Chat",
        component: () => import("@/views/chat/ChatView.vue"),
        meta: { title: "智能问答", requiresAuth: true },
      },
      {
        path: "knowledge",
        name: "Knowledge",
        component: () => import("@/views/knowledge/KnowledgeManageView.vue"),
        meta: { title: "知识库管理", requiresAuth: true },
      },
      {
        path: "stats",
        name: "Stats",
        component: () => import("@/views/stats/StatsView.vue"),
        meta: { title: "统计分析", requiresAuth: true },
      },
      {
        path: "users",
        name: "Users",
        component: () => import("@/views/user/UserManagementView.vue"),
        meta: { title: "用户管理", requiresAuth: true, requiresAdmin: true },
      },
      {
        path: "settings",
        name: "Settings",
        component: () => import("@/views/user/SystemSettingsView.vue"),
        meta: { title: "系统设置", requiresAuth: true, requiresAdmin: true },
      },
      {
        path: "trace",
        name: "Trace",
        component: () => import("@/views/trace/AgentTraceView.vue"),
        meta: { title: "Agent 编排记录", requiresAuth: true },
      },
      {
        path: "prompts",
        name: "Prompts",
        component: () => import("@/views/prompt/PromptManageView.vue"),
        meta: { title: "Prompt 管理", requiresAuth: true },
      },
    ],
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to, _from, next) => {
  document.title = `${(to.meta.title as string) || "概览"} - HazardGuard`;
  const token = localStorage.getItem("token");
  if (to.meta.requiresAuth && !token) {
    next({ name: "Login", query: { redirect: to.fullPath } });
  } else if (to.name === "Login" && token) {
    next({ name: "Dashboard" });
  } else {
    next();
  }
});

export default router;