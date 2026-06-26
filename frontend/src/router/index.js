import { createRouter, createWebHashHistory } from "vue-router";
import { useUserStore } from "@/stores/user.js";

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/home" },
    {
      path: "/home",
      name: "home",
      component: () => import("@/views/AiDialog.vue"),
    },
  ],
});

router.beforeEach((to, from, next) => {
  if (to.meta.requiresAuth) {
    const token = localStorage.getItem("authorization");
    if (token) {
      const userStore = useUserStore();
      if (
        to.meta.requiresRole &&
        !to.meta.requiresRole.includes(userStore.position)
      ) {
        next({ path: "/home" });
      } else {
        next();
      }
    } else {
      next({ path: "/home" });
    }
  } else {
    next();
  }
});

export default router;
