<template>
  <div class="report-text">
    <p :id="textId" :class="{ clamped: !expanded && collapsible }" :style="{ '--lines': lines }">{{ text }}</p>
    <button v-if="collapsible" type="button" :aria-expanded="expanded" :aria-controls="textId" @click="expanded = !expanded">
      {{ expanded ? '收起' : '展开完整内容' }}
    </button>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue';
const props = defineProps({ textId: String, text: { type: String, default: '' }, lines: { type: Number, default: 3 }, threshold: { type: Number, default: 120 } });
const expanded = ref(false);
const collapsible = computed(() => props.text.length > props.threshold || props.text.split('\n').length > props.lines);
</script>

<style scoped>
p { margin: 0; line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; }
.clamped { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: var(--lines); overflow: hidden; }
button { background: none; border: 0; color: #087589; cursor: pointer; padding: 6px 0; font: inherit; font-size: 14px; }
button:focus-visible { outline: 2px solid #0891b2; outline-offset: 3px; }
</style>
