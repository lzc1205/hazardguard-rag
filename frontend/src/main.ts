import { createApp } from "vue";
import { createPinia } from "pinia";
import ElementPlus from "element-plus";
import * as ElementPlusIconsVue from "@element-plus/icons-vue";
import "element-plus/dist/index.css";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import App from "./App.vue";
import router from "./router";
import "@/styles/index.scss";

const app = createApp(App);

// 全局注册 Element Plus 图标。
// 必须注册：概览页能力矩阵用 <component :is="c.icon" /> 以字符串名
// 动态渲染图标，未注册的组件名会解析为未知元素 → 渲染成空白方框。
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component);
}

app.use(createPinia());
app.use(router);
app.use(ElementPlus, { locale: zhCn });
app.mount("#app");