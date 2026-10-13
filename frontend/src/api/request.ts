import axios from "axios";
import { ElMessage } from "element-plus";

const request = axios.create({
  baseURL: "/api/v1",
  timeout: 30000,
});

request.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

request.interceptors.response.use(
  (response) => {
    const data = response.data;
    // 后端业务错误统一走 {code, message}
    if (data && typeof data === "object" && data.code !== 0) {
      ElMessage.error(data.message || "请求失败");
      return Promise.reject(new Error(data.message));
    }
    return data;
  },
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("token");
      window.location.href = "/login";
      return Promise.reject(error);
    }

    // **关键**：FastAPI 的 HTTPException 用的是 `detail` 字段，
    // 而业务异常用 `message`。原来只读 message，
    // 导致上传失败（400）时前端只弹「Network error」，
    // 用户完全看不到「不支持的文件类型 .doc」这种可操作的原因。
    const body = error.response?.data;
    const detail =
      (typeof body === "string" && body) ||
      body?.detail ||
      body?.message ||
      "";
    // detail 可能是数组（422 参数校验错误）
    const text = Array.isArray(detail)
      ? detail.map((d: any) => d?.msg || JSON.stringify(d)).join("；")
      : detail;

    const msg = text
      ? `${error.response?.status === 413 ? "文件过大：" : ""}${text}`
      : error.message === "Network Error"
        ? "无法连接服务器，请确认后端已启动"
        : "请求失败";

    ElMessage({ message: msg, duration: 5000, showClose: true });
    return Promise.reject(new Error(msg));
  },
);

export default request;
