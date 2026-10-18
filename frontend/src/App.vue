<script setup>
import { onMounted, nextTick, ref } from "vue";
import { useRouter } from "vue-router";
import { useUserStore } from "@/stores/user.js";
import { getThis } from "@/request/axiosForUser.js";
import { useScrollReveal } from "@/composables/useScrollReveal.js";
import NavBar from "@/components/NavBar.vue";

const userStore = useUserStore();
const authReady = ref(false);
const router = useRouter();
const { observe } = useScrollReveal();

onMounted(async () => {
  // 兼容升级：删除旧外部服务 Token，不再读取或发送它。
  localStorage.removeItem("authorization");
  try {
    userStore.setUser(await getThis());
  } catch {
    userStore.clear();
  } finally {
    authReady.value = true;
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
      <router-view v-if="authReady" v-slot="{ Component }">
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
