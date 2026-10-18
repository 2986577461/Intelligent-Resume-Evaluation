import { defineStore } from "pinia";

// 本地生成默认头像，不向第三方发送邮箱或昵称。
function defaultAvatar(name) {
  const initial = name ? Array.from(name)[0].toUpperCase().replace(/[<>&"']/g, "") : "U";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" rx="32" fill="#e0f2fe"/><text x="32" y="42" text-anchor="middle" font-family="sans-serif" font-size="30" fill="#0369a1">${initial}</text></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

export const useUserStore = defineStore("user", {
  state: () => ({ id: "", email: "", name: "", condition: false }),
  getters: {
    effectiveAvatar: (state) => defaultAvatar(state.name),
  },
  actions: {
    clear() {
      this.$reset();
    },
    setUser(data) {
      this.id = data.id;
      this.email = data.email;
      this.name = data.name;
      this.condition = true;
    },
  },
});
