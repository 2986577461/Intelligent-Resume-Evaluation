import { createApp } from "vue";
import { createPinia } from "pinia";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import App from "./App.vue";
import router from "./router";
import "./style.css";

// The HTTP entry remains for other sites. Never render a production password
// form there; use the TLS origin provided by the release build.
const publicOrigin = import.meta.env.VITE_PUBLIC_ORIGIN;
if (import.meta.env.PROD && publicOrigin && window.location.origin !== publicOrigin) {
  window.location.replace(`${publicOrigin}${window.location.pathname}${window.location.search}${window.location.hash}`);
} else {
  const app = createApp(App);
  app.use(createPinia());
  app.use(router);
  app.use(ElementPlus);
  app.mount("#app");
}
