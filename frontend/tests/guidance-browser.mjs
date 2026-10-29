// 无新增测试依赖：Node 22+ 内置 WebSocket + 本机 Chrome 的隔离临时 profile。
// 仅加载合成数据，HTTP/模型/SSE均为假服务；验证真实 Vue 页面交互而非模型质量。
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const root = fileURLToPath(new URL('..', import.meta.url));
const profile = await mkdtemp(path.join(tmpdir(), 'guidance-browser-'));
const chromePath = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
let chrome, ws, server;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
try {
  server = await createServer({ root, server: { host: '127.0.0.1', port: 0 } });
  await server.listen();
  const origin = server.resolvedUrls.local[0];
  chrome = spawn(chromePath, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-background-networking', 'about:blank'], { stdio: 'ignore' });
  let startupError;
  chrome.on('error', error => { startupError = error; });
  let port;
  for (let i = 0; i < 100; i++) {
    if (startupError) throw startupError;
    try { port = (await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; break; } catch { await sleep(100); }
  }
  assert.ok(port, 'Chrome did not start; set CHROME_PATH to a local Chromium executable');
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }));
  let id = 0;
  const pending = new Map();
  const errors = [];
  ws.addEventListener('message', ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) {
      const call = pending.get(message.id);
      if (!call) return;
      pending.delete(message.id);
      clearTimeout(call.timer);
      if (message.error) call.reject(new Error(JSON.stringify(message.error)));
      else call.resolve(message.result);
    }
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
  });
  function cdp(method, params = {}) {
    return new Promise((resolve, reject) => {
      const key = ++id;
      const timer = setTimeout(() => { pending.delete(key); reject(new Error(`CDP timeout: ${method}`)); }, 10000);
      pending.set(key, { resolve, reject, timer });
      ws.send(JSON.stringify({ id: key, method, params }));
    });
  }
  async function evaluate(expression) {
    const result = await cdp('Runtime.evaluate', { expression: `(() => eval(${JSON.stringify(expression)}))()`, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  }
  async function until(expression) {
    for (let i = 0; i < 100; i++) {
      if (await evaluate(expression)) return;
      await sleep(50);
    }
    throw new Error(`UI condition timed out: ${expression}`);
  }
  await cdp('Runtime.enable');
  await cdp('Page.enable');
  await cdp('Emulation.setDeviceMetricsOverride', { width: 1366, height: 1000, deviceScaleFactor: 1, mobile: false });
  await cdp('Page.navigate', { url: `${origin}tests/guidance-harness.html` });
  await until('document.querySelectorAll(".action-card").length === 3');
  assert.equal(await evaluate('document.querySelector(".guidance").textContent.includes("不应直接显示")'), false);
  assert.equal(await evaluate('document.querySelectorAll(".question-card[open]").length'), 0);
  assert.equal(await evaluate('document.querySelectorAll(".card-details details[open]").length'), 0);
  assert.equal(await evaluate('document.querySelector(".overview .report-text p").classList.contains("clamped")'), true);
  await evaluate('document.querySelector(".overview .report-text button").click()');
  assert.equal(await evaluate('document.querySelector(".overview .report-text p").classList.contains("clamped")'), false);
  await evaluate('document.querySelector(".card-details summary").click()');
  assert.equal(await evaluate('document.querySelector(".card-details details").open'), true);
  await evaluate('document.querySelector(".question-card summary").click(); document.querySelector(".related").click()');
  assert.equal(await evaluate('document.activeElement.classList.contains("action-card")'), true);
  await evaluate('document.querySelector(".discuss").click()');
  assert.equal(await evaluate('document.querySelector(".discussion-context").textContent.includes("A1")'), true);
  await evaluate('document.querySelector(".discussion-context button").click()');
  assert.equal(await evaluate('!!document.querySelector(".discussion-context")'), false);
  await evaluate('document.querySelector(".discuss").click(); const input = document.querySelector("textarea"); input.value = "我实际做过断开缓存测试"; input.dispatchEvent(new Event("input", { bubbles: true }));');
  await evaluate('document.querySelector(".btn-send").click()');
  await until('!!window.testStream');
  const question = await evaluate('new URL(window.testStream.url, location.origin).searchParams.get("question")');
  assert.equal(question, '我实际做过断开缓存测试');
  const reference = JSON.parse(await evaluate('new URL(window.testStream.url, location.origin).searchParams.get("discussion_context")'));
  assert.equal(reference.item.id, 'A1');
  assert.equal(await evaluate('Array.from(document.querySelectorAll(".user-bubble")).at(-1).textContent'), question);
  assert.equal(await evaluate('document.querySelectorAll(".discussion-reference").length'), 1);
  assert.equal(await evaluate('document.querySelector(".discussion-reference").textContent.includes("项目的成果与验证")'), true);
  assert.equal(await evaluate('getComputedStyle(document.querySelector(".overview .report-text p")).fontSize'), '17px');
  assert.equal(await evaluate('getComputedStyle(document.querySelector(".question-text")).fontSize'), '17px');
  assert.equal(await evaluate('!!document.querySelector(".discussion-context")'), false);
  await evaluate(`window.testStream.emit('[REPORT]' + JSON.stringify({ type: 'start', version: 1, report_id: 'report-1', context: window.fixture.report.context, start_pos: 0, end_pos: 0 })); window.testStream.emit('[TEXT]' + JSON.stringify('总体判断生成中')); window.testStream.emit('[REPORT]' + JSON.stringify({ type: 'overview', report_id: 'report-1', text: '真实增量模拟', end_pos: 7 }));`);
  await until('document.querySelectorAll(".guidance").length === 2');
  assert.match(await evaluate('document.querySelectorAll(".guidance")[1].textContent'), /正在生成/);
  assert.equal(await evaluate('document.querySelectorAll(".guidance")[1].querySelectorAll(".action-card").length'), 0);
  // 用户向上阅读后，后续生成不能抢回底部。
  await evaluate('const box = document.querySelector(".messages"); box.scrollTop = 0; box.dispatchEvent(new Event("scroll"));');
  await evaluate(`window.testStream.emit('[TEXT]' + JSON.stringify('一条已校验建议')); window.testStream.emit('[REPORT]' + JSON.stringify({ type: 'action', report_id: 'report-1', item: window.fixture.action, end_pos: 15 }));`);
  await until('document.querySelectorAll(".guidance")[1].querySelectorAll(".action-card").length === 1');
  assert.equal(await evaluate('document.querySelector(".messages").scrollTop'), 0);
  assert.match(await evaluate('document.querySelector(".btn-scroll-bottom").textContent'), /有新内容/);
  assert.equal(await evaluate('document.querySelector(".card-details details").open'), true, 'new stream must not collapse previously opened evidence');
  await evaluate(`window.testStream.emit('[REPORT]' + JSON.stringify({ type: 'incomplete', report_id: 'report-1', end_pos: 15 })); window.testStream.emit('[DONE]');`);
  await until('document.querySelectorAll(".guidance")[1].textContent.includes("报告未完成")');
  await cdp('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert.equal(await evaluate('document.documentElement.scrollWidth <= 390'), true);
  const actionWidth = await evaluate('document.querySelector(".action-card").getBoundingClientRect().width');
  assert.ok(actionWidth > 200 && actionWidth < 390, `mobile card width ${actionWidth}`);
  assert.equal(await evaluate('getComputedStyle(document.querySelector(".overview .report-text p")).fontSize'), '17px');
  // 文本按 Vue 插值展示，模型引文不能作为 HTML 执行。
  await evaluate(`window.testStream = null; const input = document.querySelector('textarea'); input.value = '再测试一次'; input.dispatchEvent(new Event('input', { bubbles: true }));`);
  await evaluate(`document.querySelector('.btn-send').click()`);
  await until('!!window.testStream');
  await evaluate(`window.testStream.emit('[REPORT]' + JSON.stringify({ type: 'start', version: 1, report_id: 'report-1', context: window.fixture.report.context, start_pos: 0, end_pos: 0 })); window.testStream.emit('[REPORT]' + JSON.stringify({ type: 'overview', report_id: 'report-1', text: '<img src=x onerror="window.injected=true">', end_pos: 0 }));`);
  await until('document.querySelectorAll(".guidance").length === 3');
  assert.equal(await evaluate('!!window.injected'), false);
  assert.equal(await evaluate('document.querySelectorAll(".guidance")[2].querySelectorAll("img").length'), 0);
  await evaluate('window.testStream.onerror()');
  await until('document.querySelectorAll(".guidance")[2].textContent.includes("报告未完成")');
  await evaluate(`sessionStorage.setItem('test-discussion-history', 'true')`);
  await cdp('Page.reload');
  await until('document.querySelectorAll(".action-card").length === 3');
  assert.equal(await evaluate('document.querySelectorAll(".guidance").length'), 1);
  assert.equal(await evaluate('document.querySelectorAll(".discussion-reference").length'), 2);
  assert.equal(await evaluate('Array.from(document.querySelectorAll(".user-bubble")).some(el => el.textContent.includes("原建议（模型判断"))'), false);
  assert.equal(await evaluate('Array.from(document.querySelectorAll(".user-bubble")).some(el => el.textContent === "这是历史中的实际提问")'), true);
  assert.equal(await evaluate('Array.from(document.querySelectorAll(".user-bubble")).some(el => el.textContent === "这是旧版中的实际提问")'), true);
  assert.deepEqual(errors, []);
  if (process.env.GUIDANCE_SCREENSHOT) {
    await evaluate(`document.querySelector('.messages').scrollTop = 0`);
    await cdp('Runtime.evaluate', { expression: 'new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))', awaitPromise: true });
    const { writeFile } = await import('node:fs/promises');
    const image = await cdp('Page.captureScreenshot', { format: 'png' });
    await writeFile(process.env.GUIDANCE_SCREENSHOT, Buffer.from(image.data, 'base64'));
    await cdp('Emulation.setDeviceMetricsOverride', { width: 1366, height: 1000, deviceScaleFactor: 1, mobile: false });
    await cdp('Runtime.evaluate', { expression: 'new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))', awaitPromise: true });
    const desktop = await cdp('Page.captureScreenshot', { format: 'png' });
    await writeFile(process.env.GUIDANCE_SCREENSHOT.replace(/\.png$/, '-desktop.png'), Buffer.from(desktop.data, 'base64'));
  }
  console.log('Browser smoke passed: history/cards, progressive disclosure, linked discussion, live/incomplete states, scroll retention, mobile layout, escaped text, refresh. All data/services mocked.');
} finally {
  ws?.close();
  if (chrome && chrome.exitCode === null) {
    const exited = new Promise(resolve => chrome.once('exit', resolve));
    chrome.kill();
    await exited;
  }
  await server?.close();
  await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
