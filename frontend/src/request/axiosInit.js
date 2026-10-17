import axios from "axios";
import { ElMessage } from "element-plus";
import { useUserStore } from "@/stores/user";
import { useVisibleStore } from "@/stores/visible";

function showOnce(msg) {
  ElMessage.closeAll();
  ElMessage.error(msg);
}

// 开发走 Vite 代理，生产通过同源 /resume 前缀反向代理到 Python。
export const RESUME_PREFIX = import.meta.env.PROD ? "/resume" : "";

const axiosResumeInstance = axios.create({
  baseURL: RESUME_PREFIX,
  timeout: 60000,
  headers: { "Content-Type": "application/json" },
  withCredentials: true,
});

axiosResumeInstance.interceptors.response.use(
  (response) => response.data,
  (error) => {
    if (!error.config?.silent) {
      if (error.response?.status === 401) {
        useUserStore().clear();
        useVisibleStore().onVisible();
      }
      const detail = error.response?.data?.detail;
      if (typeof detail === "string") showOnce(detail);
      else if (Array.isArray(detail)) showOnce("请检查邮箱格式和密码长度");
      else if (error.response?.status === 401) showOnce("请登录后再尝试");
      else if (error.response?.status === 429) showOnce("访问频率过高，请稍后尝试");
      else showOnce("请求失败，请稍后重试");
    }
    return Promise.reject(error);
  },
);

export { axiosResumeInstance };
