<template>
  <details class="discussion-reference">
    <summary>
      <span class="reference-label">关联建议 · {{ context.item.id }}</span>
      <strong>{{ context.item.subject }}</strong>
      <span class="reference-hint">查看建议内容</span>
    </summary>
    <div class="reference-body">
      <span>{{ context.position }}</span>
      <p>{{ context.item.action }}</p>
      <small>这是此前的模型建议，仍需结合原文核对。</small>
    </div>
  </details>
</template>

<script setup>
defineProps({ context: { type: Object, required: true } });
</script>

<style scoped>
.discussion-reference { width: 100%; max-width: 520px; margin-bottom: 8px; border: 1px solid #dce5e9; border-radius: 12px; background: #f8fbfc; color: #345360; overflow-wrap: anywhere; }
summary { cursor: pointer; padding: 12px 15px; display: flex; flex-direction: column; gap: 5px; list-style: none; }
summary::-webkit-details-marker { display: none; }
.reference-label { color: #627d88; font-size: 13px; }
strong { font-size: 15px; line-height: 1.6; font-weight: 600; }
.reference-hint { color: #487585; font-size: 12px; }
.reference-body { border-top: 1px solid #e3ebee; padding: 12px 15px; font-size: 15px; line-height: 1.8; }
.reference-body > span, small { font-size: 13px; color: #637a84; }
p { margin: 7px 0; white-space: pre-wrap; }
summary:focus-visible { outline: 2px solid #0891b2; outline-offset: -3px; }
</style>
