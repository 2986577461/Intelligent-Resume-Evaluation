<template>
  <div v-if="visibleStore.visible" class="modal-overlay" @click.self="visibleStore.offVisible()">
    <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="account-title">
      <button class="modal-close" aria-label="关闭" @click="visibleStore.offVisible()">×</button>
      <h2 id="account-title" class="dialog-title">智能简历评估</h2>
      <p class="dialog-subtitle">{{ isRegister ? "使用邮箱创建账号" : "邮箱登录" }}</p>
      <form class="dialog-form" @submit.prevent="handleSubmit">
        <div class="form-field">
          <label for="account-email">邮箱</label>
          <input id="account-email" v-model="email" type="email" autocomplete="email"
            maxlength="254" placeholder="you@example.com" required />
        </div>
        <div class="form-field">
          <label for="account-password">密码</label>
          <input id="account-password" v-model="password" type="password"
            :autocomplete="isRegister ? 'new-password' : 'current-password'"
            :minlength="isRegister ? 12 : 1" maxlength="128"
            :placeholder="isRegister ? '请输入 12–128 位密码' : '请输入密码'" required />
        </div>
        <div v-if="isRegister" class="form-field">
          <label for="account-confirm">确认密码</label>
          <input id="account-confirm" v-model="confirmation" type="password"
            autocomplete="new-password" minlength="12" maxlength="128" placeholder="再次输入密码" required />
        </div>
        <p v-if="isRegister" class="account-hint">当前不发送验证邮件，请确认邮箱填写正确。</p>
        <button type="submit" class="btn-primary dialog-submit" :disabled="loading">
          {{ loading ? "处理中..." : isRegister ? "注册并登录" : "登录" }}
        </button>
        <button type="button" class="account-switch" :disabled="loading" @click="toggleMode">
          {{ isRegister ? "已有账号？去登录" : "没有账号？邮箱注册" }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { useVisibleStore } from "@/stores/visible";
import { useUserStore } from "@/stores/user";
import { login, register } from "@/request/axiosForUser.js";
import { ElMessage } from "element-plus";

const visibleStore = useVisibleStore();
const userStore = useUserStore();
const isRegister = ref(false);
const email = ref("");
const password = ref("");
const confirmation = ref("");
const loading = ref(false);

function toggleMode() {
  isRegister.value = !isRegister.value;
  password.value = "";
  confirmation.value = "";
}

async function handleSubmit() {
  if (loading.value) return;
  if (isRegister.value && password.value !== confirmation.value) {
    ElMessage.warning("两次输入的密码不一致");
    return;
  }
  loading.value = true;
  try {
    const submit = isRegister.value ? register : login;
    const user = await submit({ email: email.value.trim(), password: password.value });
    userStore.setUser(user);
    visibleStore.offVisible();
    window.location.reload();
  } catch {
    // 请求拦截器统一展示错误，保留表单便于重试。
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.dialog-title {
  font-family: var(--font-heading);
  font-size: 28px;
  font-weight: 700;
  text-align: center;
  margin-bottom: 4px;
}
.dialog-subtitle {
  text-align: center;
  color: var(--color-text-secondary);
  font-size: 14px;
  margin-bottom: 28px;
}
.dialog-form {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.form-field label {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
}
.form-field input {
  padding: 12px 16px;
  border: 1.5px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 15px;
  outline: none;
  transition: border-color 0.2s;
  font-family: var(--font-body);
}
.form-field input:focus {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.1);
}
.dialog-submit {
  width: 100%;
  justify-content: center;
  padding: 14px;
  margin-top: 4px;
}
.dialog-submit:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.account-hint { font-size: 12px; color: var(--color-text-secondary); margin: 0; }
.account-switch { border: none; background: transparent; color: var(--color-accent); cursor: pointer; }
</style>
