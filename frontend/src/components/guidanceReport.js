// 展示协议 v1：只消费服务端校验后的结构化事件，不从 Markdown 猜测卡片。
export function applyReportEvent(reports, event) {
  if (event.type === 'start') {
    if (event.version !== 1 || reports.some(r => r.id === event.report_id)) return;
    reports.push({ id: event.report_id, version: 1, context: event.context, file_id: event.file_id || null,
      overview: '', actions: [], interview_questions: [], status: 'generating',
      start_pos: event.start_pos, end_pos: event.end_pos });
    return;
  }
  const report = reports.find(r => r.id === event.report_id);
  if (!report || report.status !== 'generating') return;
  report.end_pos = event.end_pos;
  if (event.type === 'overview') report.overview = event.text;
  if (event.type === 'action' && !report.actions.some(a => a.id === event.item.id)) report.actions.push(event.item);
  if (event.type === 'question') report.interview_questions.push(event.item);
  if (['complete', 'incomplete'].includes(event.type)) report.status = event.type;
}

export function reportSegments(text, reports) {
  const segments = [];
  let pos = 0;
  for (const report of reports) {
    if (report.version !== 1 || report.start_pos < pos) continue;
    if (report.start_pos > pos) segments.push({ text: text.slice(pos, report.start_pos) });
    segments.push({ report });
    pos = report.end_pos;
  }
  if (pos < text.length) segments.push({ text: text.slice(pos) });
  return segments;
}

export function finishReports(reports) {
  for (const report of reports) if (report.status === 'generating') report.status = 'incomplete';
}

export function discussionContext(selection) {
  if (!selection) return null;
  return { version: 1, position: selection.position, file_id: selection.fileId || null,
    item: { id: selection.item.id, subject: selection.item.subject, action: selection.item.action } };
}

// 仅兼容上一版前端自动拼接的固定格式，不泛化拆解普通用户消息。
export function userMessageDisplay(text, metadata) {
  if (metadata?.discussion_context?.version === 1) {
    return { text, discussion: metadata.discussion_context };
  }
  const legacy = /^针对此前「([^\n]+)」审阅的建议 (A[123])「([^\n]+)」继续讨论，不重新进行完整分析。\n(?:原简历文件 ID：([^\n]+)（若需回查，请读取这份原文）\n)?原建议（模型判断，未经核实；若与简历原文冲突请回查原文）：([\s\S]+?)\n\n([\s\S]+)$/.exec(text);
  if (!legacy) return { text, discussion: null };
  return { text: legacy[6], discussion: { version: 1, position: legacy[1], file_id: legacy[4] || null,
    item: { id: legacy[2], subject: legacy[3], action: legacy[5] } } };
}
