<template>
  <div class="ai-page">
    <div class="ai-layout">
      <div v-if="pageLoading" class="ai-loading">
        <div class="ai-loading-spinner"></div>
        <span>加载中...</span>
      </div>
      <template v-else>
        <!-- Sidebar -->
        <aside class="sidebar" :class="{ open: sidebarOpen }">
          <div class="sidebar-header">
            <h1>聊天</h1>
            <button class="btn-new" @click="newConversation()" title="新建对话">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </button>
          </div>
          <div class="conv-list">
            <div
              v-for="conv in conversations"
              :key="conv.thread_id"
              :class="[
                'conv-item',
                { active: conv.thread_id === currentThreadId },
              ]"
              @click="switchConversation(conv.thread_id)"
            >
              <div class="conv-item-title">{{ conv.title }}</div>
              <div class="conv-item-time">
                {{ formatTime(conv.updated_at) }}
              </div>
              <button
                class="btn-del"
                @click.stop="delConversation(conv.thread_id)"
              >
                ×
              </button>
            </div>
            <div
              v-if="conversations.length === 0"
              style="
                padding: 20px;
                text-align: center;
                color: var(--text-muted);
                font-size: 13px;
              "
            >
              暂无对话
            </div>
          </div>
        </aside>

        <!-- Main Chat -->
        <main class="chat-area">
          <div
            class="messages"
            ref="msgBox"
            v-if="currentThreadId"
            @scroll="onScroll"
          >
            <div
              v-for="(msg, i) in messages"
              :key="i"
              :class="['msg-wrap', msg.role]"
            >
              <div class="msg-avatar">
                <img
                  v-if="msg.role === 'user'"
                  :src="userStore.effectiveAvatar"
                  alt=""
                />
                <img
                  v-else
                  :src="msg.streaming ? '/AgentAvatar.gif' : '/AgentAvatar.png'"
                  alt="AI"
                />
              </div>
              <div v-if="msg.role === 'user'" class="msg-user-wrap">
                <div
                  v-if="msg.fileInfo"
                  class="file-badge"
                  @click="openFile(msg.fileInfo.fileId, msg.fileInfo.fileName)"
                >
                  <svg
                    class="file-badge-icon"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.5"
                    width="18"
                    height="18"
                  >
                    <path
                      d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"
                    />
                    <polyline points="14 2 14 8 20 8" />
                    <line x1="9" y1="13" x2="15" y2="13" />
                    <line x1="12" y1="10" x2="12" y2="16" />
                  </svg>
                  <span class="file-badge-name">{{
                    msg.fileInfo.fileName
                  }}</span>
                </div>
                <div class="user-bubble">{{ msg.text }}</div>
              </div>
              <template v-if="msg.role === 'ai'">
                <div class="msg-body">
                  <div
                    class="ai-text"
                    v-html="renderMarkdown(searchBefore(msg))"
                  ></div>
                  <div
                    v-if="msg.webResults != null"
                    class="search-link"
                    @click="msg.webOpen = !msg.webOpen"
                  >
                    Searched the web
                    <span class="arrow" :class="{ open: msg.webOpen }">▸</span>
                  </div>
                  <div
                    v-if="
                      msg.webOpen && msg.webResults && msg.webResults.length > 0
                    "
                    class="search-dropdown"
                  >
                    <div
                      v-for="(r, ri) in msg.webResults"
                      :key="ri"
                      class="search-dropdown-item"
                      style="cursor: default"
                    >
                      <div class="search-dropdown-title">{{ r.title }}</div>
                      <div class="search-dropdown-src">{{ r.snippet }}</div>
                    </div>
                  </div>
                  <div
                    v-if="
                      msg.webOpen &&
                      (!msg.webResults || msg.webResults.length === 0)
                    "
                    class="search-dropdown"
                  >
                    <div
                      class="search-dropdown-item"
                      style="
                        cursor: default;
                        color: var(--text-muted);
                        font-size: 13px;
                      "
                    >
                      未找到相关内容
                    </div>
                  </div>
                  <div
                    v-if="msg.searching"
                    class="search-link"
                    style="cursor: default"
                  >
                    Searching the web...
                  </div>
                  <div
                    v-if="searchAfter(msg)"
                    class="ai-text"
                    v-html="renderMarkdown(searchAfter(msg))"
                  ></div>
                  <div class="msg-toolbar" v-if="!msg.streaming">
                    <button class="btn-copy" @click="copyText(msg.text)">
                      <img src="/icons/copy.svg" class="download-icon" alt="" />
                    </button>
                  </div>
                </div>
              </template>
            </div>
            <div
              v-if="loadingMessages"
              style="
                text-align: center;
                padding: 20px;
                color: var(--text-muted);
              "
            >
              加载中...
            </div>
          </div>

          <div class="chat-empty" v-if="!currentThreadId">
            <h3>有什么我可以帮忙的？</h3>
            <p>可以问我任何问题，我会尽力为你解答。</p>
          </div>

          <div class="input-wrap">
            <div v-if="pendingFile.name" class="file-tag-bar">
              <span class="file-tag-name">{{ pendingFile.name }}</span>
              <button
                class="file-tag-remove"
                @click="clearFile"
                title="移除文件"
              >
                ×
              </button>
            </div>
            <div class="input-box">
              <div class="input-inner">
                <button
                  class="btn-upload"
                  title="上传 PDF"
                  @click="triggerUpload()"
                >
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                  >
                    <line x1="12" y1="5" x2="12" y2="19" />
                    <line x1="5" y1="12" x2="19" y2="12" />
                  </svg>
                </button>
                <input
                  type="file"
                  ref="fileInput"
                  accept=".pdf"
                  @change="onFileSelect"
                  style="display: none"
                />
                <textarea
                  ref="inputBox"
                  v-model="question"
                  @keydown.enter.exact.prevent="handleEnter"
                  @keydown.alt.enter.prevent="insertNewline"
                  @paste="onPaste"
                  placeholder="Write a message..."
                  :disabled="loading"
                  rows="1"
                  @input="autoResize"
                ></textarea>
                <button
                  class="btn-send"
                  @click="send()"
                  :disabled="loading || !question.trim()"
                  title="发送"
                >
                  <span v-if="loading" class="spinner-sm"></span>
                  <svg
                    v-else
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                  >
                    <line x1="22" y1="2" x2="11" y2="13" />
                    <polygon points="22 2 15 22 11 13 2 9 22 2" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
          <div class="chat-footer">
            Claude is AI and can make mistakes. Please double-check responses.
          </div>
        </main>
      </template>
    </div>

    <div class="toast" :class="{ hide: !toastVisible }">{{ toastMsg }}</div>
    <button
      class="btn-scroll-bottom"
      v-if="showScrollBtn"
      @click="scrollBottom()"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        width="18"
        height="18"
      >
        <polyline points="6 9 12 15 18 9" />
      </svg>
    </button>

    <!-- PDF Preview Modal -->
    <div
      v-if="previewFileUrl"
      class="preview-overlay"
      @click.self="closePreview"
    >
      <div class="preview-modal">
        <div class="preview-header">
          <span class="preview-title">{{ previewFileName }}</span>
          <button class="preview-close" @click="closePreview">×</button>
        </div>
        <iframe class="preview-iframe" :src="previewFileUrl"></iframe>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, nextTick, onMounted, onUnmounted } from "vue";
import { useUserStore } from "@/stores/user";
import { ElMessage, ElMessageBox } from "element-plus";
import { marked } from "marked";
import {
  getConversations,
  getMessages,
  deleteConversation,
} from "@/request/axiosForAi.js";
import { uploadFile } from "@/request/axiosForFiles.js";

const CHAT_STREAM_URL = "/resume/chat-stream";

const userStore = useUserStore();
const question = ref("");
const messages = ref([]);
const loading = ref(false);
const loadingMessages = ref(false);
const currentThreadId = ref("");
const conversations = ref([]);
const pageLoading = ref(true);
const sidebarOpen = ref(false);
const showScrollBtn = ref(false);
const toastVisible = ref(false);
const toastMsg = ref("");
const msgBox = ref(null);
const inputBox = ref(null);
const fileInput = ref(null);
const pendingFile = ref({ name: "", fileId: "", uploading: false });
let es = null;

function _buildMsg(role, content, searchInfo, file) {
  const m = reactive({
    role,
    text: content || "",
    streaming: false,
    searching: false,
    webResults: null,
    webOpen: false,
    searchSplitPos: null,
    fileInfo: null,
  });
  if (file) {
    m.fileInfo = { fileId: file.id, fileName: file.filename };
  }
  if (searchInfo) {
    try {
      const si =
        typeof searchInfo === "string" ? JSON.parse(searchInfo) : searchInfo;
      if (si.split_pos != null) m.searchSplitPos = si.split_pos;
      if (si.web) {
        m.webResults = si.web;
      }
    } catch {}
  }
  return m;
}

function searchBefore(msg) {
  return msg.searchSplitPos != null
    ? msg.text.substring(0, msg.searchSplitPos)
    : msg.text;
}
function searchAfter(msg) {
  return msg.searchSplitPos != null
    ? msg.text.substring(msg.searchSplitPos)
    : "";
}

function renderMarkdown(t) {
  if (!t) return "";
  return marked.parse(t);
}
function scrollBottom() {
  if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight;
}
function onScroll() {
  const b = msgBox.value;
  if (b)
    showScrollBtn.value = b.scrollTop + b.clientHeight < b.scrollHeight - 200;
}
function autoResize() {
  const e = inputBox.value;
  if (!e) return;
  e.style.height = "auto";
  e.style.height = Math.min(e.scrollHeight, 150) + "px";
}
function formatTime(iso) {
  if (!iso) return "";
  const d = new Date(iso),
    n = new Date(),
    dm = Math.floor((n - d) / 6e4);
  if (dm < 1) return "刚刚";
  if (dm < 60) return dm + "m";
  if (dm < 1440) return Math.floor(dm / 60) + "h";
  return `${d.getMonth() + 1}/${d.getDate()}`;
}
async function copyText(t) {
  try {
    await navigator.clipboard.writeText(t);
    toastMsg.value = "复制成功";
    toastVisible.value = true;
    setTimeout(() => {
      toastVisible.value = false;
    }, 2000);
  } catch {}
}

async function loadConversations() {
  try {
    conversations.value = await getConversations();
  } catch {}
}

function triggerUpload() {
  const inp = fileInput.value;
  if (inp) {
    inp.value = "";
    inp.click();
  }
}

async function onFileSelect(e) {
  const file = e.target?.files?.[0];
  if (file) await uploadPdf(file);
}

function onPaste(e) {
  const items = e.clipboardData?.items;
  if (!items) return;
  for (const item of items) {
    if (item.type === "application/pdf") {
      e.preventDefault();
      const file = item.getAsFile();
      if (file) uploadPdf(file);
      break;
    }
  }
}

async function uploadPdf(file) {
  if (pendingFile.value.uploading) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    ElMessage.warning("仅支持 PDF 文件");
    return;
  }
  pendingFile.value = { name: file.name, fileId: "", uploading: true };
  try {
    const res = await uploadFile(file, currentThreadId.value);
    pendingFile.value.fileId = res.file_id;
    toastMsg.value = `已上传：${res.filename}`;
    toastVisible.value = true;
    setTimeout(() => {
      toastVisible.value = false;
    }, 2000);
  } catch {
    pendingFile.value = { name: "", fileId: "", uploading: false };
    ElMessage.error("文件上传失败");
  }
  pendingFile.value.uploading = false;
}

function clearFile() {
  pendingFile.value = { name: "", fileId: "", uploading: false };
}

const previewFileUrl = ref("");
const previewFileName = ref("");
function openFile(fileId, fileName) {
  previewFileName.value = fileName;
  const token = localStorage.getItem("authorization") || "";
  fetch(`/resume/api/files/${fileId}`, { headers: { Authorization: token } })
    .then((res) => {
      if (!res.ok) throw new Error();
      return res.blob();
    })
    .then((blob) => {
      previewFileUrl.value = URL.createObjectURL(blob);
    })
    .catch(() => ElMessage.error("文件加载失败"));
}
function closePreview() {
  if (previewFileUrl.value) URL.revokeObjectURL(previewFileUrl.value);
  previewFileUrl.value = "";
  previewFileName.value = "";
}

async function switchConversation(threadId) {
  if (loading.value) stopStream();
  currentThreadId.value = threadId;
  messages.value = [];
  loadingMessages.value = true;
  try {
    const res = await getMessages(threadId);
    messages.value = res.map((m) =>
      _buildMsg(m.role, m.content, m.search_info, m.file),
    );
  } catch {
    messages.value = [];
  }
  loadingMessages.value = false;
  nextTick(() => scrollBottom());
  sidebarOpen.value = false;
}
function newConversation() {
  if (loading.value) stopStream();
  currentThreadId.value = "";
  messages.value = [];
  question.value = "";
  nextTick(() => inputBox.value?.focus());
}
async function delConversation(threadId) {
  try {
    await ElMessageBox.confirm("确定删除该会话？", "删除确认", {
      confirmButtonText: "删除",
      cancelButtonText: "取消",
      type: "warning",
    });
  } catch {
    return;
  }
  try {
    await deleteConversation(threadId);
    ElMessage.success("已删除");
  } catch {}
  if (currentThreadId.value === threadId) {
    currentThreadId.value = "";
    messages.value = [];
  }
  await loadConversations();
}
function stopStream() {
  if (es) {
    es.close();
    es = null;
  }
  loading.value = false;
}

let lastEnter = 0;
function handleEnter() {
  var now = Date.now();
  if (now - lastEnter < 1000) {
    send();
    lastEnter = 0;
  } else {
    lastEnter = now;
  }
}
function insertNewline() {
  question.value += "\n";
  autoResize();
}

async function send() {
  const text = question.value.trim();
  if (!text || loading.value) return;

  if (!currentThreadId.value) {
    currentThreadId.value = "session-" + Date.now();
  }
  messages.value.push({
    role: "user",
    text,
    fileInfo: pendingFile.value.fileId
      ? { fileId: pendingFile.value.fileId, fileName: pendingFile.value.name }
      : null,
  });
  question.value = "";
  autoResize();
  loading.value = true;
  nextTick(() => scrollBottom());

  const aiIdx = messages.value.length;
  messages.value.push(
    reactive({
      role: "ai",
      text: "",
      streaming: true,
      searching: false,
      webResults: null,
      webOpen: false,
      searchSplitPos: null,
    }),
  );

  const token = localStorage.getItem("authorization") || "";
  let url = `${CHAT_STREAM_URL}?question=${encodeURIComponent(text)}&thread_id=${currentThreadId.value}&token=${encodeURIComponent(token)}`;
  if (pendingFile.value.fileId) {
    url += `&file_id=${pendingFile.value.fileId}`;
    clearFile();
  }
  const evtSource = new EventSource(url);
  es = evtSource;
  evtSource.onmessage = async (e) => {
    const msg = messages.value[aiIdx];
    if (e.data === "[DONE]") {
      evtSource.close();
      es = null;
      msg.streaming = false;
      msg.searching = false;
      loading.value = false;
      await loadConversations();
      nextTick(() => inputBox.value?.focus());
    } else if (e.data.startsWith("[ERROR]")) {
      evtSource.close();
      es = null;
      msg.text = "出错：" + e.data.replace("[ERROR] ", "");
      msg.streaming = false;
      loading.value = false;
    } else if (e.data === "[SEARCHING]") {
      msg.searching = true;
      if (msg.searchSplitPos == null) msg.searchSplitPos = msg.text.length;
    } else if (e.data.startsWith("[KB_RESULT]")) {
      msg.searching = false;
      if (msg.searchSplitPos == null) msg.searchSplitPos = msg.text.length;
      try {
        msg.webResults =
          JSON.parse(e.data.replace("[KB_RESULT]", "")).results || [];
      } catch {}
    } else if (e.data.startsWith("[STATE]")) {
      // 处理后端流式状态信号
      try {
        const state = JSON.parse(e.data.replace("[STATE]", ""));
        if (state.state === "searching_web") {
          msg.searching = true;
          if (msg.searchSplitPos == null) msg.searchSplitPos = msg.text.length;
        } else if (state.state === "generating" && state.search_done) {
          msg.searching = false;
          if (msg.searchSplitPos == null) msg.searchSplitPos = msg.text.length;
          if (state.results) {
            msg.webResults = state.results;
          }
        } else if (state.state === "analyzing" || state.state === "scoring") {
          msg.searching = false;
        }
      } catch {}
    } else {
      msg.searching = false;
      const t = e.data.replace(/\\n/g, "\n");
      msg.text += t;
      nextTick(() => scrollBottom());
    }
  };
  evtSource.onerror = () => {
    evtSource.close();
    es = null;
    const msg = messages.value[aiIdx];
    if (msg) {
      msg.streaming = false;
    }
    loading.value = false;
  };
}

onMounted(async () => {
  await loadConversations();
  pageLoading.value = false;
  if (conversations.value.length > 0)
    switchConversation(conversations.value[0].thread_id);
  msgBox.value?.addEventListener("scroll", onScroll);
});
onUnmounted(() => {
  if (es) {
    es.close();
    es = null;
  }
});
</script>

<style scoped>
.ai-page {
  --primary: #0891b2;
  --primary-hover: #076e86;
  --bg: #ffffff;
  --chat-bg: #fdfdfc;
  --sidebar-bg: #f9fafb;
  --sidebar-border: #e5e7eb;
  --text: #1a1a2e;
  --text-muted: #9ca3af;
  --text-link: #9ca3af;
  --user-bubble: #f3f4f6;
  --border: #e5e7eb;
  --input-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
  --radius: 24px;
  --radius-sm: 12px;
  padding-top: var(--nav-height);
  height: 100vh;
  overflow: hidden;
  font-family:
    "Inter",
    -apple-system,
    sans-serif;
  background: var(--bg);
  color: var(--text);
  -webkit-font-smoothing: antialiased;
}

.ai-layout {
  display: flex;
  height: 100%;
}
.ai-loading {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  color: var(--text-muted);
  font-size: 14px;
}
.ai-loading-spinner {
  width: 36px;
  height: 36px;
  border: 3px solid var(--border);
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: ai-spin 0.7s linear infinite;
}
@keyframes ai-spin {
  to {
    transform: rotate(360deg);
  }
}

/* Sidebar */
.sidebar {
  width: 24%;
  min-width: 200px;
  max-width: 300px;
  flex-shrink: 0;
  height: 100%;
  background: var(--sidebar-bg);
  border-right: 1px solid var(--sidebar-border);
  display: flex;
  flex-direction: column;
}
.sidebar-header {
  padding: 16px 16px 12px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 8px;
}
.sidebar-header h1 {
  font-size: 16px;
  font-weight: 600;
  flex: 1;
}
.btn-new {
  width: 32px;
  height: 32px;
  border: none;
  background: var(--border);
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s;
}
.btn-new:hover {
  background: #d1d5db;
}
.btn-new svg {
  width: 16px;
  height: 16px;
}

.conv-list {
  flex: 2;
  overflow-y: auto;
  padding: 8px;
}
.conv-item {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
  display: flex;
  align-items: center;
  gap: 8px;
}
.conv-item:hover {
  background: #f3f4f6;
}
.conv-item.active {
  background: #e5e7eb;
}
.conv-item-title {
  font-size: 15px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}
.conv-item-time {
  font-size: 11px;
  color: var(--text-muted);
}
.btn-del {
  opacity: 0;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-muted);
  font-size: 14px;
  padding: 2px 4px;
  border-radius: 4px;
}
.conv-item:hover .btn-del {
  opacity: 1;
}
.btn-del:hover {
  background: #fee2e2;
  color: #ef4444;
}

.download-icon {
  width: 30px;
  height: 30px;
  vertical-align: middle;
  margin-right: 2px;
}

/* Chat */
.chat-area {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #fdfdfc;
  position: relative;
}
.messages {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 24px 20px 40px;
  width: 85%;
  margin: 0 auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
}
.messages::-webkit-scrollbar {
  display: none;
}

.msg-wrap {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 24px;
}
.msg-wrap.user {
  flex-direction: row-reverse;
}
.msg-avatar {
  width: 45px;
  height: 45px;
  border-radius: 50%;
  overflow: hidden;
  flex-shrink: 0;
}
.msg-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.msg-body {
  flex: 1;
  min-width: 0;
}
.msg-user-wrap {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}
.user-bubble {
  background: var(--user-bubble);
  border-radius: 18px;
  padding: 10px 16px;
  min-width: 90%;
  font-size: 20px;
  line-height: 1.6;
}

.file-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 6px 10px;
  margin-bottom: 6px;
  max-width: 280px;
  cursor: pointer;
  transition: background 0.15s;
}
.file-badge:hover {
  background: #f3f4f6;
}
.file-badge-icon {
  flex-shrink: 0;
  color: #9ca3af;
}
.file-badge-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: #374151;
}
.ai-text {
  font-size: 20px;
  line-height: 1.75;
  color: var(--text);
}
.ai-text :deep(hr) {
  border: none;
  border-top: 1px solid rgb(205, 205, 205);
  margin: 1em 0;
}

.search-link {
  display: block;
  clear: both;
  width: 100%;
  color: var(--text-link);
  font-size: 19px;
  cursor: pointer;
  user-select: none;
  margin: 12px 0 6px;
  transition: color 0.15s;
}
.search-link:hover {
  color: #1a1a2e;
}
.search-link .arrow {
  font-size: 12px;
  margin-left: 2px;
  transition: transform 0.2s;
  display: inline-block;
}
.search-link .arrow.open {
  transform: rotate(90deg);
}

.search-dropdown {
  margin: 4px 0 8px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #fafbfc;
  overflow: hidden;
  word-break: break-all;
}
.search-dropdown-item {
  padding: 10px 14px;
  border-bottom: 1px solid #f3f4f6;
}
.search-dropdown-item:last-child {
  border-bottom: none;
}
.search-dropdown-title {
  font-size: 14px;
  font-weight: 500;
  color: #1f2937;
  margin-bottom: 2px;
}
.search-dropdown-src {
  font-size: 12px;
  color: #9ca3af;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msg-toolbar {
  display: flex;
  gap: 4px;
  margin-top: 6px;
}
.btn-copy {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-muted);
  padding: 8px 8px;
  border-radius: 8px;
  transition: all 0.15s;
  display: flex;
  align-items: center;
}
.btn-copy:hover {
  background: #f3f4f6;
  color: var(--text);
}
.btn-copy svg {
  width: 26px;
  height: 26px;
}

.chat-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px;
  text-align: center;
}
.chat-empty h3 {
  font-size: 22px;
  font-weight: 600;
  margin-bottom: 8px;
}
.chat-empty p {
  color: var(--text-muted);
  max-width: 400px;
  line-height: 1.6;
}

/* Input */
.input-wrap {
  padding: 0 20px 20px;
  width: 85%;
  margin: 0 auto;
}
.input-box {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--input-shadow);
  background: #fff;
  overflow: hidden;
}
.input-inner {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 12px 16px;
}
.input-inner textarea {
  flex: 1;
  border: none;
  outline: none;
  resize: none;
  font-size: 15px;
  font-family: inherit;
  line-height: 1.5;
  min-height: 24px;
  max-height: 150px;
  padding: 4px 0;
}
.input-inner textarea::placeholder {
  color: var(--text-muted);
}

.btn-upload {
  width: 36px;
  height: 36px;
  border: none;
  background: none;
  cursor: pointer;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  transition: all 0.15s;
  flex-shrink: 0;
}
.btn-upload:hover {
  background: #f3f4f6;
  color: var(--text);
}
.btn-upload svg {
  width: 20px;
  height: 20px;
}

.file-tag-bar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 0 6px;
}
.file-tag-name {
  font-size: 12px;
  color: #374151;
}
.file-tag-remove {
  background: none;
  border: none;
  cursor: pointer;
  color: #9ca3af;
  font-size: 14px;
  padding: 0 4px;
  line-height: 1;
}
.file-tag-remove:hover {
  color: #ef4444;
}

.btn-send {
  width: 36px;
  height: 36px;
  border: none;
  background: var(--text);
  color: #fff;
  cursor: pointer;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s;
  flex-shrink: 0;
}
.btn-send:hover {
  background: #374151;
}
.btn-send:disabled {
  background: #d1d5db;
  cursor: not-allowed;
}
.btn-send svg {
  width: 16px;
  height: 16px;
}

.spinner-sm {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.chat-footer {
  text-align: center;
  padding: 12px;
  font-size: 11px;
  color: var(--text-muted);
  flex-shrink: 0;
}

/* Scroll button */
.btn-scroll-bottom {
  position: fixed;
  bottom: 160px;
  left: 50%;
  transform: translateX(-50%);
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid var(--border);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  z-index: 5;
  transition: all 0.15s;
}
.btn-scroll-bottom:hover {
  background: #fff;
  color: var(--text);
}

/* Toast */
.toast {
  position: fixed;
  top: calc(var(--nav-height) + 20px);
  left: 50%;
  transform: translateX(-50%);
  background: rgba(0, 0, 0, 0.75);
  color: #fff;
  padding: 12px 28px;
  border-radius: 10px;
  font-size: 14px;
  z-index: 100;
  pointer-events: none;
  transition: opacity 0.3s;
  opacity: 1;
}
.toast.hide {
  opacity: 0;
}

/* PDF Preview Modal */
.preview-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}
.preview-modal {
  width: 90vw;
  height: 90vh;
  background: #fff;
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.2);
}
.preview-header {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #e5e7eb;
  flex-shrink: 0;
}
.preview-title {
  flex: 1;
  font-size: 14px;
  font-weight: 500;
  color: #374151;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-close {
  background: none;
  border: none;
  font-size: 22px;
  color: #9ca3af;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
}
.preview-close:hover {
  color: #374151;
}
.preview-iframe {
  flex: 1;
  border: none;
  width: 100%;
}

@media (max-width: 768px) {
  .sidebar {
    position: fixed;
    left: 0;
    top: var(--nav-height);
    z-index: 10;
    transform: translateX(-100%);
    transition: transform 0.2s;
    box-shadow: 0 0 30px rgba(0, 0, 0, 0.1);
  }
  .sidebar.open {
    transform: translateX(0);
  }
  .messages {
    padding: 16px;
    width: 100%;
  }
  .input-wrap {
    padding: 0 12px 12px;
    width: 100%;
  }
}
</style>
