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
      "/user": "http://www.myzyitzj.cn",
      "/v3/api-docs": "http://localhost:8080",
      "/api": "http://localhost:8000",
      "/chat-stream": "http://localhost:8000",
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
