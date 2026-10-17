import { defineStore } from "pinia";

export const useVisibleStore = defineStore("visible", {
  state: () => ({ visible: false }),
  actions: {
    onVisible() {
      this.visible = true;
    },
    offVisible() {
      this.visible = false;
    },
  },
});
