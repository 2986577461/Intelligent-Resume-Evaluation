<template>
  <section class="guidance" aria-label="简历审阅报告">
    <header class="report-header">
      <div class="eyebrow">简历审阅 · 不评分</div>
      <h2>{{ report.context?.position?.value || '目标岗位' }}</h2>
      <div class="report-meta">
        <span>{{ report.context?.application_type?.value }}</span>
        <span>{{ report.context?.job_description && !report.context.job_description.inferred ? '结合已提供 JD' : '未提供 JD · 按目标岗位分析' }}</span>
      </div>
      <nav aria-label="报告内容">
        <button type="button" @click="actionsSection?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })">修改建议 {{ report.actions.length }}</button>
        <button type="button" @click="questionsSection?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })">面试准备 {{ report.interview_questions.length }}</button>
      </nav>
      <p class="report-state" role="status">{{ statusText }}</p>
    </header>

    <section class="overview">
      <h3>总体判断</h3>
      <ReportText v-if="report.overview" :text="report.overview" :lines="3" />
      <p v-else class="muted">正在生成总体判断…</p>
    </section>

    <section ref="actionsSection" class="actions-section">
      <div class="section-heading"><h3>优先修改</h3><span>建议不等于已确认的错误，请结合原文判断</span></div>
      <article v-for="item in orderedActions" :key="item.id" :ref="el => { if (el) actionElements[item.id] = el; }" class="action-card" tabindex="-1">
        <div class="card-tags"><span class="action-id">{{ item.id }}</span><span class="priority" :class="{ high: item.priority === '高' }">{{ item.priority }}优先级</span><span>{{ item.kind }}</span></div>
        <h4>{{ item.subject }}</h4>
        <div class="card-block"><h5>需要关注</h5><ReportText :text="item.observation" /></div>
        <div class="card-block next-step"><h5>建议怎么做</h5><ReportText :text="item.action" /></div>
        <div class="card-details">
          <details><summary>原文依据 <span>{{ item.evidence.length }}</span></summary><blockquote v-for="(quote, index) in item.evidence" :key="index">{{ quote }}</blockquote><p v-if="!item.evidence.length" class="muted">这是缺失信息的判断，没有对应引文；不代表你没有相关经历或能力。</p></details>
          <details><summary>为什么重要</summary><p>{{ item.impact }}</p></details>
        </div>
        <button type="button" class="discuss" :disabled="busy" @click="$emit('discuss', item)">讨论这条 <span aria-hidden="true">↗</span></button>
      </article>
      <p v-if="!report.actions.length" class="empty-note">{{ report.status === 'complete' ? '本次未识别出需要优先修改的问题，不为凑数量制造缺点。' : report.status === 'incomplete' ? '未生成完整建议，请勿将缺少建议理解为没有问题。' : '建议校验完成后将逐条展示，不预设建议数量。' }}</p>
    </section>

    <section ref="questionsSection" class="questions-section">
      <div class="section-heading"><h3>面试准备</h3><span>这些是准备方向，不是新增的简历缺点</span></div>
      <details v-for="(item, index) in report.interview_questions" :key="index" class="question-card">
        <summary><span class="question-number">{{ String(index + 1).padStart(2, '0') }}</span><span><strong>{{ item.subject }}</strong><span class="question-text">{{ item.question }}</span></span><span class="expand-icon" aria-hidden="true">＋</span></summary>
        <div class="question-body">
          <button v-if="item.action_id" type="button" class="related" @click="focusAction(item.action_id)">查看关联建议 {{ item.action_id }} ↑</button>
          <h5>考察点</h5><p>{{ item.purpose }}</p>
          <h5>准备方向</h5><p>{{ item.preparation }}</p>
          <details><summary>原文依据</summary><blockquote v-for="(quote, qi) in item.evidence" :key="qi">{{ quote }}</blockquote></details>
        </div>
      </details>
      <p v-if="!report.interview_questions.length" class="empty-note">{{ report.status === 'complete' ? '本次没有额外面试追问。' : report.status === 'incomplete' ? '追问可能尚未生成完毕。' : '追问生成并校验后在这里展示。' }}</p>
    </section>
    <footer>以上为模型基于简历的建议，未核实经历真实性，不代表实际能力或录用结果。</footer>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue';
import ReportText from './ReportText.vue';
const props = defineProps({ report: { type: Object, required: true }, busy: Boolean });
defineEmits(['discuss']);
const actionsSection = ref(null);
const questionsSection = ref(null);
const actionElements = {};
const orderedActions = computed(() => [...props.report.actions].sort((a, b) => ({ 高: 0, 中: 1, 低: 2 }[a.priority] - { 高: 0, 中: 1, 低: 2 }[b.priority])));
const statusText = computed(() => ({ generating: '正在生成 · 已展示内容仍可能不完整', complete: '报告生成完成 · 请核对建议是否适用', incomplete: '报告未完成 · 当前仅为已生成部分，可重新请求完整分析' }[props.report.status]));
function focusAction(id) {
  actionElements[id]?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  actionElements[id]?.focus({ preventScroll: true });
}
</script>

<style scoped>
.guidance { width: 100%; font-size: 17px; color: var(--text, #24323b); overflow-wrap: anywhere; }
.report-header { border: 1px solid #d7e8eb; background: linear-gradient(120deg, #f2fafb, #fafcfc); border-radius: 16px; padding: 22px; }
.eyebrow { color: #4e7580; font-size: 14px; letter-spacing: .08em; }
h2 { font-size: 24px; margin: 10px 0; line-height: 1.5; } h3 { font-size: 20px; margin: 0; } h4 { font-size: 20px; margin: 12px 0 18px; line-height: 1.6; } h5 { font-size: 15px; color: #53666e; margin: 0 0 6px; font-weight: 600; }
.report-meta, .card-tags { display: flex; gap: 8px 12px; align-items: center; flex-wrap: wrap; font-size: 14px; color: #596d75; }
nav { display: flex; gap: 8px; margin-top: 18px; flex-wrap: wrap; }
button { font: inherit; cursor: pointer; } button:disabled { opacity: .45; cursor: not-allowed; }
nav button { border: 1px solid #c5dce0; background: #fff; color: #236271; border-radius: 20px; padding: 6px 13px; font-size: 14px; }
.report-state { margin: 14px 0 0; font-size: 14px; color: #62767d; }
.overview { margin: 24px 0; } .overview h3 { margin-bottom: 10px; }
.section-heading { display: flex; align-items: baseline; gap: 8px 14px; flex-wrap: wrap; margin: 26px 0 12px; }.section-heading > span { color: #6c7c82; font-size: 14px; }
.action-card { border: 1px solid #e1e6e9; border-radius: 14px; padding: 20px; margin: 12px 0; background: #fff; scroll-margin: 20px; }
.action-id { color: #526d75; font-weight: 700; }.priority { background: #f0f3f5; color: #596570; padding: 3px 8px; border-radius: 5px; }.priority.high { background: #fff3dc; color: #835a16; }
.card-block { margin-top: 16px; }.next-step { padding: 14px; border-radius: 9px; background: #f4f9f9; }
.card-details { border-top: 1px solid #eef1f3; margin-top: 16px; padding-top: 8px; }
details { margin: 8px 0; } summary { cursor: pointer; color: #506c76; font-size: 15px; line-height: 1.7; } p { line-height: 1.8; white-space: pre-wrap; }
blockquote { margin: 10px 0; padding: 10px 13px; background: #f7f8fa; border-left: 3px solid #c9d8dd; font-size: 16px; line-height: 1.8; white-space: pre-wrap; }
.discuss, .related { border: 0; background: transparent; color: #087589; padding: 7px 0; font-size: 15px; }.discuss { margin-top: 8px; }
.question-card { border: 1px solid #e1e6e9; border-radius: 12px; margin: 10px 0; }.question-card > summary { display: flex; align-items: baseline; gap: 12px; padding: 16px; list-style: none; }.question-card > summary::-webkit-details-marker { display: none; }.question-card > summary > span:nth-child(2) { flex: 1; }.question-card strong { font-size: 17px; font-weight: 600; }.question-text { display: block; color: #344b55; font-size: 17px; margin-top: 7px; }.question-number { color: #78919a; }.expand-icon { color: #78919a; }.question-card[open] .expand-icon { transform: rotate(45deg); }.question-body { border-top: 1px solid #edf1f3; padding: 12px 20px; }.question-body h5 { margin-top: 14px; }
.empty-note, .muted { font-size: 16px; color: #667b84; line-height: 1.8; }.empty-note { border: 1px dashed #d7e1e5; border-radius: 10px; padding: 16px; }
footer { border-top: 1px solid #e8edef; margin-top: 25px; padding-top: 15px; font-size: 14px; line-height: 1.8; color: #71828a; }
button:focus-visible, summary:focus-visible, article:focus-visible { outline: 2px solid #0891b2; outline-offset: 3px; }
@media (max-width: 600px) { .report-header, .action-card { padding: 16px; } h2 { font-size: 22px; } h4 { font-size: 19px; } .question-card > summary { gap: 8px; padding: 13px; } }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
</style>
