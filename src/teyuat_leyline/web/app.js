/* 提瓦特地脉 · Teyuat Leyline 前端控制逻辑 */
"use strict";

const api = () => window.pywebview && window.pywebview.api;

const state = {
  tasks: new Map(),
  threads: 8,
  saveDir: "",
};

const STATUS_LABEL = {
  queued: "排队中",
  probing: "探测中",
  downloading: "下载中",
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
  const count = 46;
  for (let i = 0; i < count; i++) {
    const s = document.createElement("span");
    s.className = "star";
    s.style.left = Math.random() * 100 + "%";
    s.style.top = Math.random() * 100 + "%";
    s.style.setProperty("--d", (3 + Math.random() * 6).toFixed(1) + "s");
    s.style.animationDelay = (Math.random() * 6).toFixed(1) + "s";
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

  const card = el(`
    <div class="task-card" data-id="${esc(task.id)}">
      <div class="task-top">
        <div class="task-name" title="${esc(task.url)}">${esc(task.filename)}</div>
        <div class="badge ${badgeClass(st)}">${STATUS_LABEL[st] || st}</div>
      </div>
      <div class="progress"><div class="progress-fill" style="width:${pct.toFixed(2)}%"></div></div>
      <div class="task-meta">
        <span>${fmtBytes(task.downloaded)} / ${fmtBytes(total)}</span>
        <span>${task.threads || 1} 线程</span>
        <span class="spacer"></span>
        ${active && task.speed ? `<span class="meta-speed">${fmtSpeed(task.speed)}</span>` : ""}
        ${active && total > 0 ? `<span class="meta-eta">剩余 ${fmtEta(task.eta)}</span>` : ""}
      </div>
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
  return card;
}

function renderTasks() {
  const listEl = document.getElementById("taskList");
  const emptyEl = document.getElementById("empty");
  listEl.innerHTML = "";
  const arr = Array.from(state.tasks.values());
  if (arr.length === 0) {
    emptyEl.style.display = "block";
    listEl.style.display = "none";
  } else {
    emptyEl.style.display = "none";
    listEl.style.display = "flex";
    arr.forEach((t) => listEl.appendChild(renderCard(t)));
  }
  updateStats();
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

function upsert(task) {
  if (!task) return;
  state.tasks.set(task.id, task);
  renderTasks();
}

function forget(task) {
  if (!task) return;
  state.tasks.delete(task.id);
  renderTasks();
}

/* ---------- PyWebview 事件回推 ---------- */
window.__teyuatUI = function (msg) {
  const { event, task } = msg || {};
  if (event === "forget") forget(task);
  else upsert(task);
};

/* ---------- 初始化 & 交互 ---------- */
function bindUI() {
  document.getElementById("addBtn").addEventListener("click", addTaskFromInput);
  document.getElementById("urlInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") addTaskFromInput();
  });
  document.getElementById("chooseDir").addEventListener("click", chooseDir);
  document.getElementById("threadsUp").addEventListener("click", () => stepThreads(1));
  document.getElementById("threadsDown").addEventListener("click", () => stepThreads(-1));
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
    await api().add_task(url);
    input.value = "";
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
  state.threads = cfg.numThreads || 8;
  state.saveDir = cfg.saveDir || "";
  document.getElementById("threadsVal").textContent = state.threads;
  document.getElementById("statDir").textContent = state.saveDir || "—";
}

/* 兜底刷新：即使事件丢失也能拿到最终状态 */
async function refresh() {
  if (!api()) return;
  try {
    const tasks = await api().get_tasks();
    const kept = new Map();
    tasks.forEach((t) => { kept.set(t.id, t); });
    state.tasks = kept;
    renderTasks();
  } catch (_) { /* ignore */ }
}

function boot() {
  renderStars();
  bindUI();
  if (api()) {
    api().get_config().then(applyConfig).catch(() => {});
    refresh();
  }
}

window.addEventListener("pywebviewready", () => {
  document.title = "提瓦特地脉 · Teyuat Leyline";
  boot();
});

window.addEventListener("DOMContentLoaded", () => {
  renderStars();
  bindUI();
  setInterval(refresh, 1500);
});
