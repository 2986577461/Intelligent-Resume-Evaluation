import axios from "axios";
import { ElMessage } from "element-plus";

function showOnce(msg) {
  ElMessage.closeAll();
  ElMessage.error(msg);
}

// 生产 → /resume 前缀让 nginx 路由到 resume-backend
// 开发 → 空，走 Vite proxy（vite.config.js）
export const RESUME_PREFIX = import.meta.env.PROD ? "/resume" : "";

const axiosInstance = axios.create({
  baseURL: "",
  timeout: 60000,
  headers: { "Content-Type": "application/json" },
  withCredentials: true,
});

const axiosResumeInstance = axios.create({
  baseURL: RESUME_PREFIX,
  timeout: 60000,
  headers: { "Content-Type": "application/json" },
  withCredentials: true,
});

function setupInterceptors(instance) {
  instance.interceptors.request.use((config) => {
    const token = localStorage.getItem("authorization");
    if (token) config.headers.Authorization = token;
    return config;
  });

  instance.interceptors.response.use(
    (response) => {
      const body = response.data;
      if (body && body.code != null && Number(body.code) !== 200) {
        showOnce(body.msg);
      }
      return body;
    },
    (error) => {
      if (error.response?.status === 401) {
        const token = localStorage.getItem("authorization");
        showOnce(token ? "身份验证失败，请重新登录!" : "请登录后再尝试!");
      } else if (error.response?.status === 500) {
        showOnce("服务器内部出错！");
      } else if (error.response?.status === 503) {
        showOnce("访问频率过高，请稍后尝试！");
      }
      return Promise.reject(error);
    },
  );
}

setupInterceptors(axiosInstance);
setupInterceptors(axiosResumeInstance);

export default axiosInstance;
export { axiosResumeInstance };
