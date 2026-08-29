/* 提瓦特地脉 · Teyvat Leyline 前端控制逻辑 */
"use strict";

const api = () => window.pywebview && window.pywebview.api;

const state = {
  tasks: new Map(),
  threads: 8,
  saveDir: "",
  config: {},
};

/* 卡片 DOM 节点缓存：progress 事件只更新对应卡片，避免整表重建 */
const cardNodes = new Map(); // taskId -> Element

const STATUS_LABEL = {
  queued: "排队中",
  probing: "探测中",
  downloading: "下载中",
  checking: "校验中",
  paused: "已暂停",
  completed: "已完成",
  error: "异常中断",
  cancelled: "已取消",
};

/* ---------- 工具 ---------- */
function fmtBytes(n) {
  if (n == null) return "—";
  if (n === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (v >= 100 ? v.toFixed(0) : v.toFixed(1)) + " " + units[i];
}

function fmtSpeed(bps) {
  if (!bps || bps <= 0) return "0 B/s";
  return fmtBytes(bps) + "/s";
}

function fmtEta(sec) {
  if (sec == null || !isFinite(sec) || sec <= 0) return "—";
  if (sec < 60) return (sec | 0) + " 秒";
  const m = sec / 60;
  if (m < 60) return (m | 0) + " 分 " + ((sec % 60) | 0) + " 秒";
  return (m / 60).toFixed(1) + " 小时";
}

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/* ---------- 渲染 ---------- */
function renderStars() {
  const wrap = document.getElementById("stars");
  wrap.innerHTML = "";
  const count = 24;
  for (let i = 0; i < count; i++) {
    const s = document.createElement("span");
    s.className = "star";
    s.style.left = Math.random() * 100 + "%";
    s.style.top = Math.random() * 100 + "%";
    wrap.appendChild(s);
  }
}

function badgeClass(status) {
  return status;
}

function renderCard(task) {
  const total = task.total || 0;
  const pct = total > 0 ? Math.min(100, (task.downloaded / total) * 100) : 0;
  const st = task.status;
  const active = st === "downloading" || st === "probing";
  // 进行中 / 已暂停的任务可实时调整每任务限速
  const showRate = active || st === "paused" || st === "queued";

  const card = el(`
    <div class="task-card" data-id="${esc(task.id)}">
      <div class="task-top">
        <div class="task-name" title="${esc(task.url)}">${esc(task.filename)}</div>
        <div class="badge ${badgeClass(st)}">${STATUS_LABEL[st] || st}</div>
      </div>
      <div class="progress"><div class="progress-fill" style="width:${pct.toFixed(2)}%"></div></div>
      <div class="task-meta">
        <span class="meta-dl">${fmtBytes(task.downloaded)} / ${fmtBytes(total)}</span>
        <span class="meta-threads">${task.threads || 1} 线程</span>
        <span class="spacer"></span>
        ${active && task.speed ? `<span class="meta-speed">${fmtSpeed(task.speed)}</span>` : ""}
        ${active && total > 0 ? `<span class="meta-eta">剩余 ${fmtEta(task.eta)}</span>` : ""}
      </div>
      ${showRate ? `
      <div class="task-rate">
        <span class="row-label">限速</span>
        <button class="step-btn" data-rate="-1" aria-label="减少限速">−</button>
        <span class="rate-val" data-rate-num>${task.speedKbps || 0}</span>
        <button class="step-btn" data-rate="+1" aria-label="增加限速">＋</button>
        <span class="row-unit">KB/s · 0=不限</span>
      </div>` : ""}
      ${st === "completed" && task.verified === true ? `<div class="verify-line ok">✓ 完整性校验通过（SHA256）</div>` : ""}
      ${task.error ? `<div class="task-error">${esc(task.error)}</div>` : ""}
      <div class="task-actions"></div>
    </div>`);

  const actions = card.querySelector(".task-actions");
  const addBtn = (label, cls, fn, disabled) => {
    const b = el(`<button class="mini-btn ${cls}">${label}</button>`);
    if (disabled) b.disabled = true;
    b.addEventListener("click", fn);
    actions.appendChild(b);
  };

  if (st === "downloading" || st === "probing" || st === "queued") {
    addBtn("暂停", "", () => api().pause_task(task.id));
    addBtn("取消", "danger", () => api().cancel_task(task.id));
  } else if (st === "paused") {
    addBtn("继续", "", () => api().resume_task(task.id));
    addBtn("取消", "danger", () => api().cancel_task(task.id));
  } else if (st === "error") {
    addBtn("重试", "", () => api().resume_task(task.id));
    addBtn("移除", "danger", () => api().remove_task(task.id));
  } else if (st === "completed") {
    addBtn("移除", "danger", () => api().remove_task(task.id));
  } else if (st === "cancelled") {
    addBtn("移除", "danger", () => api().remove_task(task.id));
  }

  // 每任务限速步进
  card.querySelectorAll("[data-rate]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const delta = parseInt(btn.dataset.rate, 10);
      const next = Math.max(0, (task.speedKbps || 0) + delta);
      if (api()) api().set_task_speed(task.id, next);
      const num = card.querySelector("[data-rate-num]");
      if (num) num.textContent = next;
    });
  });
  return card;
}

function showEmpty() {
  const listEl = document.getElementById("taskList");
  const emptyEl = document.getElementById("empty");
  emptyEl.style.display = "block";
  listEl.style.display = "none";
}

/* 结构字段变化（需重建卡：按钮/徽章/错误提示）才走全量渲染 */
function isStructural(prev, task) {
  return (
    !prev ||
    prev.status !== task.status ||
    prev.error !== task.error ||
    prev.filename !== task.filename ||
    prev.threads !== task.threads
  );
}

/* 纯进度变化：原地更新进度条与文本节点，避免重建卡片 DOM 与其内按钮 */
function patchProgress(card, task) {
  const total = task.total || 0;
  const pct = total > 0 ? Math.min(100, (task.downloaded / total) * 100) : 0;
  const fill = card.querySelector(".progress-fill");
  if (fill) fill.style.width = pct.toFixed(2) + "%";
  const dl = card.querySelector(".meta-dl");
  if (dl) dl.textContent = fmtBytes(task.downloaded) + " / " + fmtBytes(total);
  const threads = card.querySelector(".meta-threads");
  if (threads) threads.textContent = (task.threads || 1) + " 线程";
  const speed = card.querySelector(".meta-speed");
  if (speed) speed.textContent = fmtSpeed(task.speed);
  const eta = card.querySelector(".meta-eta");
  if (eta) eta.textContent = "剩余 " + fmtEta(task.eta);
}

function upsert(task) {
  if (!task || !task.id) return;
  const prev = state.tasks.get(task.id);
  // 快照未变化则跳过，避免高频重复更新
  if (prev && JSON.stringify(prev) === JSON.stringify(task)) return;
  state.tasks.set(task.id, task);
  notifyTransition(prev, task);

  const listEl = document.getElementById("taskList");
  let node = cardNodes.get(task.id);
  if (node) {
    if (isStructural(prev, task)) {
      const fresh = renderCard(task);
      node.replaceWith(fresh);
      node = fresh;
    } else {
      patchProgress(node, task);
    }
  } else {
    node = renderCard(task);
    listEl.appendChild(node);
  }
  cardNodes.set(task.id, node);
  updateStats();
}

function forget(task) {
  if (!task || !task.id) return;
  state.tasks.delete(task.id);
  const node = cardNodes.get(task.id);
  if (node) node.remove();
  cardNodes.delete(task.id);
  if (cardNodes.size === 0) showEmpty();
  updateStats();
}

/* 与后端全量同步：按返回的快照增量更新，删除本地多余的卡片 */
function reconcile(taskList) {
  const seen = new Set();
  (taskList || []).forEach((t) => {
    if (t && t.id) { seen.add(t.id); upsert(t); }
  });
  for (const id of [...cardNodes.keys()]) {
    if (!seen.has(id)) forget({ id });
  }
}

function updateStats() {
  const arr = Array.from(state.tasks.values());
  const active = arr.filter((t) => t.status === "downloading" || t.status === "probing");
  const speed = active.reduce((s, t) => s + (t.speed || 0), 0);
  document.getElementById("statTotal").textContent = arr.length;
  document.getElementById("statActive").textContent = active.length;
  document.getElementById("statSpeed").textContent = fmtSpeed(speed);
  document.getElementById("statDir").textContent = state.saveDir || "—";
}

/* ---------- 通知 toast ---------- */
function toast(message, type = "info") {
  const box = document.getElementById("toasts");
  const t = el(`<div class="toast ${type}">${esc(message)}</div>`);
  box.appendChild(t);
  setTimeout(() => {
    t.classList.add("out");
    setTimeout(() => t.remove(), 300);
  }, 3800);
}

/* ---------- 状态跳变提示 ---------- */
function notifyTransition(prev, task) {
  if (!prev) return; // 启动首帧快照，不弹
  if (task.status === "completed" && prev.status !== "completed") {
    const ok = task.verified === true;
    toast(`「${task.filename}」下载完成${ok ? "，哈希校验通过" : ""}`, ok ? "success" : "info");
  } else if (task.status === "error" && prev.status !== "error") {
    toast(`「${task.filename}」下载异常中断`, "error");
  }
}

/* ---------- 初始化 & 交互 ---------- */
function clamp(v, lo, hi, def) {
  return Number.isFinite(v) ? Math.max(lo, Math.min(hi, v)) : def;
}

function bindUI() {
  document.getElementById("addBtn").addEventListener("click", addTaskFromInput);
  document.getElementById("urlInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") addTaskFromInput();
  });
  document.getElementById("chooseDir").addEventListener("click", chooseDir);
  document.getElementById("threadsUp").addEventListener("click", () => stepThreads(1));
  document.getElementById("threadsDown").addEventListener("click", () => stepThreads(-1));
  document.getElementById("openSettings").addEventListener("click", openSettings);
  document.getElementById("settingsClose").addEventListener("click", closeSettings);
  document.getElementById("settingsCancel").addEventListener("click", closeSettings);
  document.getElementById("settingsSave").addEventListener("click", saveSettings);
  document.getElementById("cfgHash").addEventListener("click", (e) => {
    const on = e.currentTarget.getAttribute("aria-checked") === "true";
    e.currentTarget.setAttribute("aria-checked", on ? "false" : "true");
  });
  document.getElementById("settingsModal").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeSettings();
  });
}

function stepThreads(delta) {
  const next = Math.max(1, Math.min(16, state.threads + delta));
  state.threads = next;
  document.getElementById("threadsVal").textContent = next;
  if (api()) api().set_threads(next);
}

async function addTaskFromInput() {
  const input = document.getElementById("urlInput");
  const url = input.value.trim();
  if (!url) { input.focus(); return; }
  if (api()) {
    const res = await api().add_task(url);
    input.value = "";
    if (res && !res.ok && res.known) toast("该链接已在下载历史中，已跳过避免重复下载", "info");
    else if (res && !res.ok && res.error) toast(res.error, "error");
  }
}

async function chooseDir() {
  if (!api()) return;
  const res = await api().choose_directory();
  if (res && res.ok) {
    state.saveDir = res.path;
    document.getElementById("statDir").textContent = res.path;
  }
}

async function applyConfig(cfg) {
  if (!cfg) return;
  state.config = cfg;
  state.threads = cfg.numThreads || 8;
  state.saveDir = cfg.saveDir || "";
  document.getElementById("threadsVal").textContent = state.threads;
  document.getElementById("statDir").textContent = state.saveDir || "—";
}

/* 设置弹窗 */
function openSettings() {
  const c = state.config || {};
  document.getElementById("cfgThreads").value = c.numThreads ?? 8;
  document.getElementById("cfgSpeed").value = c.globalSpeedKbps ?? 0;
  document.getElementById("cfgConcurrent").value = c.maxConcurrent ?? 4;
  document.getElementById("cfgRetries").value = c.maxRetries ?? 3;
  document.getElementById("cfgDelay").value = c.retryDelay ?? 2;
  document.getElementById("cfgProxy").value = c.proxy ?? "";
  document.getElementById("cfgHash").setAttribute("aria-checked", c.hashCheck ? "true" : "false");
  document.getElementById("settingsModal").classList.remove("hidden");
}

function closeSettings() {
  document.getElementById("settingsModal").classList.add("hidden");
}

async function saveSettings() {
  const settings = {
    numThreads: clamp(parseInt(document.getElementById("cfgThreads").value, 10), 1, 16, 8),
    globalSpeedKbps: clamp(parseInt(document.getElementById("cfgSpeed").value, 10), 0, 100000, 0),
    maxConcurrent: clamp(parseInt(document.getElementById("cfgConcurrent").value, 10), 1, 64, 4),
    maxRetries: clamp(parseInt(document.getElementById("cfgRetries").value, 10), 0, 99, 3),
    retryDelay: clamp(parseFloat(document.getElementById("cfgDelay").value), 0, 3600, 2),
    proxy: document.getElementById("cfgProxy").value.trim(),
    hashCheck: document.getElementById("cfgHash").getAttribute("aria-checked") === "true",
  };
  if (api()) {
    const res = await api().update_settings(settings);
    if (res && res.config) {
      state.config = res.config;
      state.threads = res.config.numThreads || state.threads;
      document.getElementById("threadsVal").textContent = state.threads;
    }
  }
  closeSettings();
}

/* 进度/状态刷新：前端定时轮询后端全量快照，避免后台线程直接调用 webview */
async function refresh() {
  if (!api()) return;
  try {
    const tasks = await api().get_tasks();
    reconcile(tasks || []);
  } catch (_) { /* ignore */ }
}

/* 初始化：静态 UI 只绑定一次（避免 DOMContentLoaded 与 pywebviewready 重复执行）。
   进度采用定时轮询 refresh() 更新，页面无每帧动画、无后台线程触碰 webview。 */
let _booted = false;
function ensureUI() {
  if (_booted) return;
  _booted = true;
  renderStars();
  bindUI();
}

function boot() {
  ensureUI();
  if (api()) {
    api().get_config().then(applyConfig).catch(() => {});
    refresh();
    setInterval(refresh, 800);
  }
}

window.addEventListener("pywebviewready", () => {
  document.title = "提瓦特地脉 · Teyvat Leyline";
  boot();
});

window.addEventListener("DOMContentLoaded", ensureUI);
