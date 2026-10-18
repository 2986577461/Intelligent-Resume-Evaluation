import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  base: "",
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    proxy: {
      // 保留浏览器 Host，让后端同源 Cookie/Origin 校验与开发代理保持一致。
      "/api": { target: "http://localhost:8000", changeOrigin: false },
      "/chat-stream": { target: "http://localhost:8000", changeOrigin: false },
      "/download": "http://localhost:8000",
      // Dev: /resume 前缀只在生产用，dev 去掉前缀转发
      "/resume/api": {
        target: "http://localhost:8000",
        rewrite: (path) => path.replace(/^\/resume/, ""),
      },
      "/resume/chat-stream": {
        target: "http://localhost:8000",
        rewrite: (path) => path.replace(/^\/resume/, ""),
      },
    },
  },
});
