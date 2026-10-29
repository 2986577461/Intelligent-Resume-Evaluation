import test from 'node:test';
import assert from 'node:assert/strict';
import { applyReportEvent, reportSegments, finishReports, discussionContext, userMessageDisplay } from '../src/components/guidanceReport.js';

const start = { type: 'start', version: 1, report_id: 'r1', context: { position: { value: '后端' } }, start_pos: 3, end_pos: 3 };
test('live events and persisted report give the same view, including emoji offsets', () => {
  const reports = [];
  applyReportEvent(reports, start);
  applyReportEvent(reports, { type: 'overview', report_id: 'r1', text: '总体判断', end_pos: 6 });
  const action = { id: 'A1', subject: '验证', action: '补充已有验证' };
  applyReportEvent(reports, { type: 'action', report_id: 'r1', item: action, end_pos: 6 });
  applyReportEvent(reports, { type: 'question', report_id: 'r1', item: { question: '如何验证' }, end_pos: 6 });
  applyReportEvent(reports, { type: 'complete', report_id: 'r1', end_pos: 6 });
  const text = '😀前报告文后续';
  const segments = reportSegments(text, reports);
  assert.equal(segments[0].text, '😀前');
  assert.equal(segments[1].report.status, 'complete');
  assert.equal(segments[2].text, '后续');
  const restored = JSON.parse(JSON.stringify(reports));
  finishReports(restored);
  assert.deepEqual(reportSegments(text, restored), segments);
});

test('duplicate starts/actions cannot create duplicate cards, old metadata falls back to Markdown', () => {
  const reports = [];
  applyReportEvent(reports, start);
  applyReportEvent(reports, start);
  const event = { type: 'action', report_id: 'r1', item: { id: 'A1' }, end_pos: 3 };
  applyReportEvent(reports, event);
  applyReportEvent(reports, event);
  assert.equal(reports.length, 1);
  assert.equal(reports[0].actions.length, 1);
  assert.deepEqual(reportSegments('旧报告', []), [{ text: '旧报告' }]);
  assert.deepEqual(reportSegments('未来版本', [{ version: 2 }]), [{ text: '未来版本' }]);
});

test('interruption cannot become a complete report because DONE arrived later', () => {
  const reports = [];
  applyReportEvent(reports, start);
  finishReports(reports);
  applyReportEvent(reports, { type: 'complete', report_id: 'r1', end_pos: 9 });
  assert.equal(reports[0].status, 'incomplete');
});

test('multiple reports preserve surrounding conversational content', () => {
  const reports = [{ version: 1, start_pos: 1, end_pos: 3 }, { version: 1, start_pos: 4, end_pos: 6 }];
  assert.deepEqual(reportSegments('前报告中报告后', reports), [{ text: '前' }, { report: reports[0] }, { text: '中' }, { report: reports[1] }, { text: '后' }]);
});

test('discussion metadata keeps raw question separate and is restored without prompt text', () => {
  assert.equal(discussionContext(null), null);
  const context = discussionContext({ position: '后端', fileId: 'file-1', item: { id: 'A1', subject: '验证', action: '补充证据', evidence: ['不需要发送的其他字段'] } });
  assert.deepEqual(context, { version: 1, position: '后端', file_id: 'file-1', item: { id: 'A1', subject: '验证', action: '补充证据' } });
  assert.deepEqual(userMessageDisplay('我测过了', { discussion_context: context }), { text: '我测过了', discussion: context });
  assert.deepEqual(userMessageDisplay('普通问题', null), { text: '普通问题', discussion: null });
});

test('previous auto-generated prompt is displayed as a reference, without modifying ordinary messages', () => {
  const header = '针对此前「后端」审阅的建议 A1「验证」继续讨论，不重新进行完整分析。\n';
  const source = '原简历文件 ID：file-1（若需回查，请读取这份原文）\n';
  const advice = '原建议（模型判断，未经核实；若与简历原文冲突请回查原文）：补充证据\n\n';
  for (const file of ['', source]) {
    const result = userMessageDisplay(header + file + advice + '我测过了\n\n还有一点', null);
    assert.equal(result.text, '我测过了\n\n还有一点');
    assert.equal(result.discussion.item.id, 'A1');
    assert.equal(result.discussion.item.action, '补充证据');
  }
  const ordinary = '这是引用，不是自动关联：\n' + header + advice + '问题';
  assert.equal(userMessageDisplay(ordinary, null).text, ordinary);
});
