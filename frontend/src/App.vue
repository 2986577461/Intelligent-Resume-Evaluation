<script setup>
import { onMounted, nextTick } from "vue";
import { useRouter } from "vue-router";
import { useUserStore } from "@/stores/user.js";
import { getThis } from "@/request/axiosForUser.js";
import { useScrollReveal } from "@/composables/useScrollReveal.js";
import NavBar from "@/components/NavBar.vue";

const userStore = useUserStore();
const router = useRouter();
const { observe } = useScrollReveal();

onMounted(async () => {
  if (localStorage.getItem("authorization")) {
    try {
      const resp = await getThis();
      if (resp.code === "200") {
        userStore.setUser(resp.data);
      }
    } catch (e) {
      // ignore
    }
  }

  await router.isReady();
  await nextTick();
  observe();

  router.afterEach(async () => {
    await nextTick();
    observe();
  });
});
</script>

<template>
  <div class="app">
    <NavBar />
    <div id="main-content" class="page-wrap">
      <router-view v-slot="{ Component }">
        <transition name="page">
          <component :is="Component" />
        </transition>
      </router-view>
    </div>
  </div>
</template>

<style scoped>
.app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}
.page-wrap {
  flex: 1;
}
</style>

<style></style>
