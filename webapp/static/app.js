"use strict";

const $ = (id) => document.getElementById(id);
const state = { token: "", status: null, selected: "", page: 1, total: 0, pending: false,
  groupSignature: "", dataStamp: 0, qrJob: "", qrUrl: "", messageRequest: 0, lastAnalysis: "", lastCapture: "" };
let pollTimer, pollBusy = false, searchTimer, toastTimer;

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 6500);
}

async function api(path, options = {}) {
  const headers = { "X-Local-Token": state.token, ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "本地服务暂时无法完成请求。");
  }
  return response.json();
}

function formatTime(value) {
  if (!value) return "—";
  return new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });
}

function updateButtons() {
  const status = state.status;
  const busy = state.pending || Boolean(status?.busy);
  const canCapture = !busy && Boolean(status?.credential_saved);
  ["capture-full", "capture-update", "capture-resume"].forEach((id) => { $(id).disabled = !canCapture; });
  $("login-button").disabled = busy || !status?.environment_ready;
  ["download-csv", "download-json"].forEach((id) => { $(id).disabled = !state.selected; });
  $("analyze-button").disabled = busy || !state.selected;
  $("monitor-button").disabled = state.pending || (!status?.monitor.enabled && (busy || !state.selected));
}

function clearQr() {
  if (state.qrUrl) URL.revokeObjectURL(state.qrUrl);
  state.qrUrl = "";
  state.qrJob = "";
  $("login-qr").hidden = true;
  $("login-qr").removeAttribute("src");
  $("qr-placeholder").hidden = false;
}

async function showQr(job) {
  if (state.qrJob === job.id) return;
  const response = await fetch("/api/login/qr", { headers: { "X-Local-Token": state.token }, cache: "no-store" });
  if (!response.ok) return;
  const blob = await response.blob();
  if (state.status?.jobs[0]?.id !== job.id || !state.status.busy) return;
  clearQr();
  state.qrJob = job.id;
  state.qrUrl = URL.createObjectURL(blob);
  $("login-qr").src = state.qrUrl;
  $("login-qr").hidden = false;
  $("qr-placeholder").hidden = true;
}

function renderJob(status) {
  const job = status.jobs[0];
  $("cancel-job").hidden = !status.busy;
  if (!job) return;
  const labels = { running: "进行中", succeeded: "已完成", failed: "未完成", cancelled: "已取消" };
  $("job-status").className = `job-status ${job.status}`;
  const description = element("div");
  description.append(element("strong", "", `${job.title} · ${labels[job.status]}`), element("p", "", job.message));
  $("job-status").replaceChildren(element("span", "job-icon", job.status === "running" ? "↻" : "↳"), description);
  $("job-logs").replaceChildren(...job.logs.slice(-4).map((log) => element("li", "", log.text)));
  if (job.kind === "login" && job.status === "running") showQr(job).catch(() => {});
  else clearQr();
  if (job.kind === "analysis" && job.status === "succeeded" && job.id !== state.lastAnalysis) {
    state.lastAnalysis = job.id;
    if (job.group_id === state.selected) loadAnalysis();
  }
  if (job.kind === "capture" && job.status === "succeeded" && job.id !== state.lastCapture) {
    state.lastCapture = job.id;
    selectGroup(job.group_id);
  }
}

function selectGroup(groupId, fillForm = true) {
  state.selected = groupId;
  state.page = 1;
  state.dataStamp = 0;
  $("saved-group").value = groupId;
  const group = state.status?.groups.find((item) => item.id === groupId);
  if (!groupId) {
    $("message-list").replaceChildren();
    $("record-empty").hidden = false;
    $("pagination").hidden = true;
    $("record-range").textContent = "请选择一个已保存的群";
  }
  if (fillForm && group) {
    $("chat-url").value = `https://www.xiaohongshu.com/chat/${groupId}`;
    $("group-name").value = group.name === groupId ? "" : group.name;
  }
  loadMessages();
  loadAnalysis();
  updateButtons();
}

function renderGroups(status) {
  const signature = JSON.stringify(status.groups.map(({ id, name }) => ({ id, name })));
  if (signature !== state.groupSignature) {
    state.groupSignature = signature;
    const placeholder = element("option", "", "选择已保存的群");
    placeholder.value = "";
    $("saved-group").replaceChildren(placeholder, ...status.groups.map((group) => {
      const option = element("option", "", group.name);
      option.value = group.id;
      return option;
    }));
    if (!state.selected && status.groups.length) selectGroup(status.groups[0].id, false);
    else $("saved-group").value = state.selected;
  }
  const group = status.groups.find((item) => item.id === state.selected);
  if (group && group.updated_at !== state.dataStamp) {
    state.dataStamp = group.updated_at;
    $("record-range").textContent = group.earliest && group.latest
      ? `${group.earliest.slice(0, 10)} — ${group.latest.slice(0, 10)} · ${group.completed ? "历史已读完" : "历史可继续采集"}`
      : "本地记录已保存";
    loadMessages();
  }
}

function renderMonitor(status) {
  const monitor = status.monitor;
  $("stat-monitor").textContent = monitor.enabled ? `${monitor.interval_minutes} 分钟` : "未开启";
  $("monitor-hint").textContent = monitor.enabled ? "定时增量更新运行中" : "完成首次采集后可开启";
  $("monitor-state").textContent = monitor.enabled ? "运行中" : "已暂停";
  $("monitor-state").className = monitor.enabled ? "badge" : "badge neutral";
  $("monitor-button").textContent = monitor.enabled ? "暂停定时更新" : "开启定时更新";
  if (monitor.last_error) $("monitor-detail").textContent = monitor.last_error;
  else if (monitor.enabled) {
    const group = status.groups.find((item) => item.id === monitor.group_id);
    $("monitor-detail").textContent = `${group?.name || monitor.group_id} · 下次更新：${formatTime(monitor.next_run)}`;
  } else $("monitor-detail").textContent = "发生采集错误时会自动暂停；服务重启后需手动开启。";
}

async function poll() {
  clearTimeout(pollTimer);
  if (pollBusy) return;
  pollBusy = true;
  try {
    const status = await api("/api/status");
    state.status = status;
    $("service-badge").textContent = "本地服务已启动";
    $("stat-groups").textContent = status.groups.length.toLocaleString();
    $("stat-messages").textContent = status.total_messages.toLocaleString();
    $("login-state").textContent = status.credential_saved ? "登录凭据已保存" : "等待扫码登录";
    $("login-indicator").className = status.credential_saved ? "state-dot connected" : "state-dot";
    $("login-button").textContent = status.credential_saved ? "重新扫码登录" : "生成登录二维码";
    $("login-message").textContent = status.credential_saved
      ? `上次扫码：${formatTime(status.last_login)}。登录失效时可重新扫码。`
      : (!status.busy && status.last_login_attempt?.status === "failed"
        ? status.last_login_attempt.message : "账号凭据只保存在本机，不会显示在页面中。");
    renderJob(status);
    renderGroups(status);
    renderMonitor(status);
    updateButtons();
  } catch (error) {
    $("service-badge").textContent = "本地服务连接中断";
  } finally {
    pollBusy = false;
    pollTimer = setTimeout(poll, 2000);
  }
}

async function perform(action, message = "任务已开始。") {
  state.pending = true;
  updateButtons();
  try {
    await action();
    toast(message);
    await poll();
  } catch (error) { toast(error.message); }
  finally { state.pending = false; updateButtons(); }
}

function capture(mode) {
  if (!$("capture-form").reportValidity()) return;
  perform(() => api("/api/capture", { method: "POST", body: JSON.stringify({
    chat_url: $("chat-url").value.trim(), name: $("group-name").value.trim(),
    mode, max_pages: Number($("max-pages").value),
  }) }));
}

async function loadMessages() {
  const sequence = ++state.messageRequest;
  if (!state.selected) return;
  const params = new URLSearchParams({ search: $("search").value, page: state.page, size: 30 });
  try {
    const result = await api(`/api/groups/${state.selected}/messages?${params}`);
    if (sequence !== state.messageRequest) return;
    state.total = result.total;
    $("message-list").replaceChildren(...result.items.map((message) => {
      const row = element("article", "message-row");
      const main = element("div", "message-main");
      const meta = element("div", "message-meta");
      const name = message.nickname || "群成员";
      meta.append(element("span", "message-name", name), element("time", "message-time", message.timestamp || "时间未知"));
      main.append(meta, element("p", "message-text", message.revoked ? "这条消息已撤回" : (message.text || "非文字消息")));
      if (message.quoted_text) main.append(element("p", "quote", message.quoted_text));
      if (message.media_kind) main.append(element("span", "media-label", ({ image: "图片", voice: "语音", video: "视频", sticker: "表情", file: "文件" })[message.media_kind] || message.media_kind));
      row.append(element("span", "avatar", name.slice(0, 1)), main);
      return row;
    }));
    $("record-empty").hidden = result.items.length > 0;
    $("record-empty").querySelector("h3").textContent = $("search").value ? "没有找到匹配的消息" : "这个群暂时没有消息";
    $("record-empty").querySelector("p").textContent = $("search").value ? "试试其他关键词，或清空搜索条件。" : "可以获取新增消息，或继续采集更早的历史。";
    $("pagination").hidden = result.total === 0;
    $("record-total").textContent = `共 ${result.total.toLocaleString()} 条消息 · 最新在前`;
    $("page-label").textContent = `第 ${state.page} / ${Math.max(1, Math.ceil(result.total / 30))} 页`;
    $("page-prev").disabled = state.page <= 1;
    $("page-next").disabled = state.page * 30 >= result.total;
  } catch (error) { if (sequence === state.messageRequest) toast(error.message); }
}

async function loadAnalysis() {
  $("topic-list").replaceChildren();
  $("analysis-downloads").hidden = true;
  $("analysis-empty").hidden = false;
  if (!state.selected) return;
  const selected = state.selected;
  try {
    const result = await api(`/api/groups/${selected}/analysis`);
    if (selected !== state.selected) return;
    const ready = Object.keys(result.summary).length > 0;
    $("analysis-empty").textContent = ready ? "已完成整理，未命中当前主题词表。" : "点击“生成分析”，整理当前群的文字讨论。";
    $("analysis-empty").hidden = result.topics.length > 0;
    $("topic-list").replaceChildren(...result.topics.slice(0, 8).map((topic) => {
      const tag = element("span", "topic-tag", topic.topic);
      tag.append(element("strong", "", `${topic.matched_messages} 条`));
      return tag;
    }));
    $("analysis-downloads").hidden = !ready;
  } catch (error) { toast(error.message); }
}

async function download(kind) {
  if (!state.selected) return;
  try {
    const response = await fetch(`/api/groups/${state.selected}/download/${kind}`, { headers: { "X-Local-Token": state.token } });
    if (!response.ok) { const body = await response.json(); throw new Error(body.detail); }
    const url = URL.createObjectURL(await response.blob());
    const anchor = element("a");
    anchor.href = url;
    anchor.download = `${state.selected}-${kind}.${kind === "json" ? "json" : "csv"}`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { toast(error.message); }
}

$("capture-form").addEventListener("submit", (event) => { event.preventDefault(); capture("full"); });
$("capture-update").addEventListener("click", () => capture("update"));
$("capture-resume").addEventListener("click", () => capture("resume"));
$("login-button").addEventListener("click", () => { clearQr(); perform(() => api("/api/login", { method: "POST" }), "正在生成二维码，请稍候。"); });
$("cancel-job").addEventListener("click", () => { const job = state.status?.jobs[0]; if (job) perform(() => api(`/api/jobs/${job.id}/cancel`, { method: "POST" }), "正在取消任务。"); });
$("saved-group").addEventListener("change", (event) => selectGroup(event.target.value));
$("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.page = 1; loadMessages(); }, 250); });
$("page-prev").addEventListener("click", () => { state.page -= 1; loadMessages(); });
$("page-next").addEventListener("click", () => { state.page += 1; loadMessages(); });
$("download-csv").addEventListener("click", () => download("csv"));
$("download-json").addEventListener("click", () => download("json"));
document.querySelectorAll("[data-download]").forEach((button) => button.addEventListener("click", () => download(button.dataset.download)));
$("analyze-button").addEventListener("click", () => perform(() => api(`/api/groups/${state.selected}/analyze`, { method: "POST" })));
$("monitor-button").addEventListener("click", () => {
  const enabled = !state.status.monitor.enabled;
  perform(() => api("/api/monitor", { method: "POST", body: JSON.stringify({
    enabled, group_id: enabled ? state.selected : state.status.monitor.group_id,
    interval_minutes: Number($("interval").value), max_pages: Number($("max-pages").value),
  }) }), enabled ? "定时增量更新已开启。" : "定时增量更新已暂停。");
});
document.querySelectorAll(".nav-link").forEach((link) => link.addEventListener("click", () => {
  document.querySelectorAll(".nav-link").forEach((item) => item.classList.toggle("active", item === link));
}));

async function boot() {
  updateButtons();
  try {
    const response = await fetch("/api/bootstrap", { cache: "no-store" });
    if (!response.ok) throw new Error("无法连接本地服务，请刷新页面。");
    const data = await response.json();
    state.token = data.token;
    await poll();
  } catch (error) { toast(error.message); }
}
boot();
