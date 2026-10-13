import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "path";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // 后端 API 地址。改后端端口时这里要同步改，
        // 或直接用 `python scripts\start.py` 启动（会自动同步）
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  css: {
    preprocessorOptions: {
      scss: {
        additionalData: `@use "@/styles/variables.scss" as *;`,
      },
    },
  },
  build: {
    // 把体积大且很少变动的第三方库从业务代码里拆出来。
    // 起因：主包 1.2MB（gzip 390KB），首屏要等全部下载完。
    // 拆开后 vendor 命中浏览器缓存，业务代码改动不会让用户重下 Element Plus。
    rollupOptions: {
      output: {
        manualChunks: {
          vue: ["vue", "vue-router", "pinia"],
          element: ["element-plus", "@element-plus/icons-vue"],
        },
      },
    },
    chunkSizeWarningLimit: 700,
  },
});