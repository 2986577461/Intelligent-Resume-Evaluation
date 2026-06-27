import axios from "axios";
import { ElMessage } from "element-plus";

let toastTimer = null;
function showOnce(msg) {
  ElMessage.closeAll();
  ElMessage.error(msg);
}

// 生产：空 baseURL，请求走同域 Nginx 路由
// 开发：空 baseURL，请求走 Vite proxy（vite.config.js）
//    /user/* /admin/* → localhost:8080 (Java)
//    /api/* /chat-stream → localhost:8000 (Python)
const axiosInstance = axios.create({
  baseURL: "",
  timeout: 60000,
  headers: { "Content-Type": "application/json" },
  withCredentials: true,
});

// 带 /resume 前缀的实例（用于简历评估项目前端）
const axiosResumeInstance = axios.create({
  baseURL: "/resume",
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
