"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const commentsView = location.pathname === "/comments";
  const selectedNote = commentsView ? (new URLSearchParams(location.search).get("note") || "") : "";
  const state = { token: "", status: null, revision: null, page: 1, size: 20, total: 0,
    pending: false, request: 0, detail: "", detailRequest: 0, scope: null, creator: "", monitors: [], previewUrl: "" };
  let timer, filterTimer, toastTimer, polling = false;
  const node = (tag, className = "", text = "") => {
    const item = document.createElement(tag);
    item.className = className;
    item.textContent = text;
    return item;
  };
  const date = value => value ? new Date(value * 1000).toLocaleString("zh-CN", { hour12: false }) : "时间未提供";
  const jobLabels = { running: "进行中", succeeded: "已完成", failed: "未完成", cancelled: "已取消" };
  function toast(message) {
    $("toast").textContent = message;
    $("toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $("toast").hidden = true; }, 6500);
  }
  async function api(path, options = {}) {
    const response = await fetch(path, { ...options, cache: "no-store", headers: {
      "X-Local-Token": state.token, ...(options.body ? { "Content-Type": "application/json" } : {})
    } });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "本地服务暂时无法完成请求。");
    }
    return response.json();
  }
  function updateButtons() {
    const busy = state.pending || Boolean(state.status?.busy);
    const ready = Boolean(state.status?.credential_saved && state.status?.notes_environment_ready);
    const captureLabel = Number($("comment-limit").value) ? "采集详情与评论" : "采集笔记详情";
    $("link-submit").textContent = captureLabel;
    $("detail-capture").textContent = captureLabel;
    document.querySelectorAll("[data-capture], #search-submit, #link-submit, #detail-capture, #creator-submit, #detail-media, #detail-creator").forEach(button => {
      button.disabled = busy || !ready;
      if (button.dataset.capture && !button.dataset.detailed) button.textContent = captureLabel;
    });
    $("refresh-comments").disabled = busy || !ready || !state.scope;
    $("export-notes-csv").disabled = !state.total;
    $("export-notes-json").disabled = !state.total;
  }
  function renderJob() {
    const status = state.status;
    const job = status.jobs[0];
    $("capture-state").textContent = status.busy ? "任务进行中" : "空闲";
    $("cancel-note-job").hidden = !status.busy;
    if (!job) return;
    const description = node("div");
    description.append(node("strong", "", `${job.title} · ${jobLabels[job.status] || job.status}`), node("p", "", job.message));
    $("note-job").className = "job-status " + job.status;
    $("note-job").replaceChildren(node("span", "job-icon", job.status === "running" ? "↻" : "↳"), description);
  }
  async function startJob(request) {
    state.pending = true;
    updateButtons();
    try {
      const job = await api("/api/notes/jobs", { method: "POST", body: JSON.stringify(request) });
      state.status.busy = true;
      state.status.jobs.unshift(job);
      renderJob();
      toast("采集任务已开始，结果会自动更新。");
    } catch (error) { toast(error.message); }
    finally { state.pending = false; updateButtons(); }
  }
  function capture(id) {
    return startJob({ mode: "detail", note_id: id, max_comments: Number($("comment-limit").value),
      include_replies: $("include-replies").checked, max_replies: Number($("reply-limit").value) });
  }
  function noteRow(note) {
    const row = node("article", "note-row");
    row.dataset.noteId = note.id;
    const symbol = node("span", "note-symbol", note.kind === "video" ? "视频" : "图文");
    symbol.setAttribute("aria-hidden", "true");
    const content = node("div", "note-content");
    const heading = node("div", "note-heading");
    const title = node("button", "note-title", note.title || "无标题笔记");
    title.addEventListener("click", () => openDetail(note.id));
    heading.append(title, node("span", "note-label" + (note.detailed ? " full" : ""), note.detailed ? "已采集详情" : "搜索摘要"));
    const meta = node("div", "note-meta");
    meta.append(node("span", "", note.author || "作者未提供"), node("span", "", `赞 ${note.likes || 0}`),
      node("span", "", `收藏 ${note.collects || 0}`), node("span", "", `本地评论与回复 ${note.saved_comments}`));
    if (note.source_keyword) meta.append(node("span", "", "来源：" + note.source_keyword));
    const actions = node("div", "note-actions");
    const collect = node("button", "button small", note.detailed ? "重新采集" : "采集详情与评论");
    collect.dataset.capture = note.id;
    if (note.detailed) collect.dataset.detailed = "true";
    collect.addEventListener("click", () => capture(note.id));
    const comments = node("a", "button small", "查看评论");
    comments.href = "/comments?note=" + encodeURIComponent(note.id);
    actions.append(collect, comments);
    content.append(heading, node("p", "note-excerpt", note.description || "搜索摘要未包含正文，采集详情后可查看。"), meta, actions);
    row.append(symbol, content);
    return row;
  }
  function commentRow(comment) {
    const row = node("article", "comment-card");
    const meta = node("div", "message-meta");
    meta.append(node("strong", "message-name", comment.author || "未提供昵称"), node("span", "message-time", date(comment.published_at)));
    const foot = node("div", "note-meta");
    foot.append(node("span", "", `赞 ${comment.likes || 0}`), node("span", "", comment.parent_id ? "二级回复" : `一级评论 · ${comment.reply_count || 0} 条回复`));
    row.append(meta, node("p", "comment-body", comment.content || "[非文字评论]"), foot);
    if (comment.parent_id) row.append(node("blockquote", "quote", "回复的一级评论：" + (comment.parent_content || "未保存原文")));
    if (!selectedNote) {
      const link = node("a", "comment-note", "来自：" + (comment.note_title || "无标题笔记"));
      link.href = "/comments?note=" + encodeURIComponent(comment.note_id);
      row.append(link);
    }
    return row;
  }
  async function loadList() {
    const request = ++state.request;
    $("notes-list").setAttribute("aria-busy", "true");
    const params = new URLSearchParams({ page: state.page, size: state.size, search: $("local-filter").value });
    if (selectedNote) params.set("note_id", selectedNote);
    if (commentsView) params.set("level", $("comment-level").value);
    try {
      const data = await api((commentsView ? "/api/note-comments?" : "/api/notes?") + params);
      if (request !== state.request) return;
      state.total = data.total;
      $("list-error").hidden = true;
      $("notes-list").replaceChildren(...data.items.map(commentsView ? commentRow : noteRow));
      $("note-empty").hidden = Boolean(data.items.length);
      $("empty-title").textContent = $("local-filter").value ? "没有找到匹配记录" : commentsView ? "还没有保存的一级评论" : "还没有本地笔记";
      $("empty-description").textContent = $("local-filter").value ? "换一个关键词，或清空本地搜索条件。" : commentsView ? "到笔记采集页面选取笔记，并设置大于零的评论上限。" : "搜索关键词保存摘要，或粘贴笔记链接采集完整内容。";
      $("list-total").textContent = `${data.total} ${commentsView ? "条" : "篇"}`;
      $("notes-pagination").hidden = !data.total;
      $("notes-page-count").textContent = `显示 ${data.items.length} 条 · 共 ${data.total} 条`;
      $("notes-page-label").textContent = `${state.page} / ${Math.max(1, Math.ceil(data.total / state.size))}`;
      $("notes-prev").disabled = state.page <= 1;
      $("notes-next").disabled = state.page * state.size >= data.total;
      updateButtons();
    } catch (error) {
      if (request !== state.request) return;
      $("list-error").textContent = error.message;
      $("list-error").hidden = false;
    } finally { if (request === state.request) $("notes-list").setAttribute("aria-busy", "false"); }
  }
  async function loadScope() {
    if (!selectedNote) return;
    try {
      const note = await api("/api/notes/" + encodeURIComponent(selectedNote));
      state.scope = note;
      $("scope-title").textContent = note.title || "无标题笔记";
      $("scope-status").textContent = (!note.comments_checked_at ? "尚未采集一级评论。" :
        `上次采集：${date(note.comments_checked_at)} · 本次 ${note.last_scan_count} 条一级评论 · ${note.comments_complete ? "已读到分页末尾" : "达到采集上限"}。`) +
        (note.replies_checked_at ? ` 回复采集：${date(note.replies_checked_at)}，本轮 ${note.last_reply_count} 条，${note.replies_complete ? "所选评论的回复已读完" : "达到回复上限"}。` : "尚未采集二级回复。") + "旧记录保留。";
    } catch (error) { $("scope-title").textContent = error.message; }
    finally { updateButtons(); }
  }
  async function openDetail(id, refresh = false) {
    const request = ++state.detailRequest;
    try {
      const note = await api("/api/notes/" + encodeURIComponent(id));
      if (request !== state.detailRequest || (refresh && !$("note-detail").open)) return;
      if (state.detail !== id) clearPreview();
      state.detail = id;
      state.creator = /^[a-f0-9]{24}$/.test(note.creator_id || "") ? note.creator_id : "";
      $("detail-creator").hidden = !state.creator;
      $("detail-title").textContent = note.title || "无标题笔记";
      $("detail-kind").textContent = note.detailed ? "已保存完整详情" : "搜索摘要";
      $("detail-meta").textContent = `${note.author || "作者未提供"} · ${date(note.published_at)} · 赞 ${note.likes || 0} · 收藏 ${note.collects || 0}`;
      $("detail-summary").hidden = note.detailed;
      $("detail-summary").textContent = "当前保存的是搜索摘要。点击下方按钮获取完整正文和一级评论。";
      $("detail-body").textContent = note.description || "暂未保存文字正文。";
      $("detail-comments").href = "/comments?note=" + encodeURIComponent(id);
      $("detail-original").href = "https://www.xiaohongshu.com/explore/" + id;
      renderMedia(note.media || [], id);
      if (!$("note-detail").open) $("note-detail").showModal();
      updateButtons();
    } catch (error) { toast(error.message); }
  }
  async function poll() {
    clearTimeout(timer);
    if (polling) return;
    polling = true;
    try {
      const status = await api("/api/status");
      state.status = status;
      $("account-state").textContent = status.credential_saved ? "凭据已保存 · 管理登录" : "未登录 · 前往扫码";
      $("account-state").className = status.credential_saved ? "badge" : "badge neutral";
      $("notes-count").textContent = status.notes_summary.notes.toLocaleString();
      $("comments-count").textContent = status.notes_summary.comments.toLocaleString();
      $("setup-warning").hidden = status.notes_environment_ready && status.credential_saved;
      $("setup-warning").textContent = !status.notes_environment_ready ? "笔记采集环境尚未安装，请运行项目中的 Install-Notes.ps1 后重启服务。" : "请先返回群记录工作台扫码登录，再采集笔记和评论。";
      renderJob();
      updateButtons();
      if (commentsView) await loadMonitors();
      if (state.revision !== status.notes_summary.revision) {
        state.revision = status.notes_summary.revision;
        await Promise.all([loadList(), loadScope()]);
        if ($("note-detail").open && state.detail) await openDetail(state.detail, true);
      }
    } catch (error) { $("account-state").textContent = "服务连接中断 · 刷新重试"; }
    finally { polling = false; timer = setTimeout(poll, 2000); }
  }
  async function download(kind) {
    const params = new URLSearchParams({ dataset: commentsView ? "comments" : "notes", kind, search: $("local-filter").value });
    if (selectedNote) params.set("note_id", selectedNote);
    if (commentsView) params.set("level", $("comment-level").value);
    try {
      const response = await fetch("/api/notes/export?" + params, { headers: { "X-Local-Token": state.token }, cache: "no-store" });
      if (!response.ok) throw new Error("导出未完成，请刷新后重试。");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `xhs-${commentsView ? "comments" : "notes"}.${kind}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { toast(error.message); }
  }
  function clearPreview() {
    $("media-preview").replaceChildren();
    $("media-preview").hidden = true;
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = "";
  }
  function renderMedia(files, noteId) {
    const container = $("detail-media-list");
    container.replaceChildren();
    if (!files.length) {
      container.append(node("p", "field-hint", "尚未下载媒体。点击下方“下载图片 / 视频”保存到本机。"));
      return;
    }
    files.forEach(file => {
      const row = node("div", "media-file");
      row.append(node("span", "", `${file.name} · ${(file.bytes / 1024 / 1024).toFixed(2)} MB`));
      for (const preview of [true, false]) {
        const button = node("button", "button small", preview ? "预览" : "下载文件");
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            const response = await fetch(`/api/notes/${noteId}/media/${encodeURIComponent(file.name)}`, { headers: { "X-Local-Token": state.token } });
            if (!response.ok) throw new Error("文件无法读取，可能已被移动，请重新下载媒体。");
            const blob = await response.blob();
            if (preview) {
              if (!$("note-detail").open || state.detail !== noteId) return;
              clearPreview();
              state.previewUrl = URL.createObjectURL(blob);
              const media = document.createElement(file.kind === "video" ? "video" : "img");
              if (file.kind === "video") { media.controls = true; media.preload = "metadata"; }
              else media.alt = "已下载的笔记图片";
              media.src = state.previewUrl;
              $("media-preview").append(media);
              $("media-preview").hidden = false;
            } else {
              const url = URL.createObjectURL(blob), link = document.createElement("a");
              link.href = url; link.download = file.name; link.click();
              setTimeout(() => URL.revokeObjectURL(url), 1000);
            }
          } catch (error) { toast(error.message); }
          finally { button.disabled = false; }
        });
        row.append(button);
      }
      container.append(row);
    });
  }
  let monitorInitialized = false;
  async function loadMonitors() {
    const data = await api("/api/note-monitors");
    state.monitors = data.items;
    const selected = data.items.find(item => item.note_id === selectedNote);
    if (selected && !monitorInitialized) {
      for (const [id, value] of [["note-monitor-interval", selected.interval_minutes], ["refresh-limit", selected.max_comments], ["refresh-reply-limit", selected.max_replies]]) {
        if (![...$(id).options].some(option => option.value === String(value))) $(id).add(new Option(String(value), String(value)));
        $(id).value = String(value);
      }
      $("refresh-replies").checked = selected.include_replies;
    }
    monitorInitialized = true;
    $("note-monitor-badge").textContent = selected?.enabled ? "已开启" : "已暂停";
    $("note-monitor-toggle").textContent = selected?.enabled ? "暂停定时检查" : "开启定时检查";
    $("note-monitor-state").textContent = selected?.last_error || (selected?.enabled ? `下次检查：${date(selected.next_run)}。其他任务进行时会顺延。` : "最多同时监测 10 篇。暂停后当前采集会继续，可通过上方按钮取消任务。");
    const list = $("note-monitors-list");
    list.replaceChildren();
    if (!data.items.length) list.append(node("p", "field-hint", "从笔记的“查看评论”进入单篇评论页，即可设置定时检查。"));
    data.items.forEach(item => {
      const row = node("div", "monitor-row");
      const label = node("div"), link = node("a", "text-button", item.title || "无标题笔记");
      link.href = "/comments?note=" + encodeURIComponent(item.note_id);
      label.append(link, node("p", "field-hint", item.last_error || (item.enabled ? `每 ${item.interval_minutes} 分钟 · 下次 ${date(item.next_run)}` : "已暂停")));
      const button = node("button", "button small", item.enabled ? "暂停" : "恢复");
      button.addEventListener("click", () => saveMonitor(!item.enabled, item));
      row.append(label, button); list.append(row);
    });
  }
  async function saveMonitor(enabled, saved = null) {
    const body = saved ? { note_id: saved.note_id, enabled, interval_minutes: saved.interval_minutes,
      max_comments: saved.max_comments, include_replies: saved.include_replies, max_replies: saved.max_replies } : {
      note_id: selectedNote, enabled, interval_minutes: Number($("note-monitor-interval").value),
      max_comments: Number($("refresh-limit").value), include_replies: $("refresh-replies").checked,
      max_replies: Number($("refresh-reply-limit").value)
    };
    try { await api("/api/note-monitors", { method: "POST", body: JSON.stringify(body) }); await loadMonitors(); toast(enabled ? "设置已保存，定时检查已开启。" : "设置已保存，定时检查已暂停。"); }
    catch (error) { toast(error.message); }
  }
  $("creator-form").addEventListener("submit", event => {
    event.preventDefault();
    startJob({ mode: "creator", url: $("creator-url").value, max_notes: Number($("creator-limit").value) });
  });
  $("detail-media").addEventListener("click", () => { if (state.detail) startJob({ mode: "media", note_id: state.detail, max_comments: 0 }); });
  $("detail-creator").addEventListener("click", () => {
    if (state.creator) { startJob({ mode: "creator", url: state.creator, max_notes: Number($("creator-limit").value) }); $("note-detail").close(); }
  });
  $("comment-level").addEventListener("change", () => { state.page = 1; loadList(); });
  $("note-monitor-toggle").addEventListener("click", () => saveMonitor(!state.monitors.find(item => item.note_id === selectedNote)?.enabled));
  $("note-monitor-save").addEventListener("click", () => saveMonitor(Boolean(state.monitors.find(item => item.note_id === selectedNote)?.enabled)));
  $("note-search-form").addEventListener("submit", event => {
    event.preventDefault();
    startJob({ mode: "search", keyword: $("keyword").value, sort: $("note-sort").value, max_notes: Number($("note-limit").value), note_type: Number($("search-note-type").value) });
  });
  $("note-link-form").addEventListener("submit", event => {
    event.preventDefault();
    startJob({ mode: "detail", url: $("note-url").value, max_comments: Number($("comment-limit").value),
      include_replies: $("include-replies").checked, max_replies: Number($("reply-limit").value) });
  });
  $("refresh-comments").addEventListener("click", () => startJob({ mode: "comments", note_id: selectedNote, max_comments: Number($("refresh-limit").value),
    include_replies: $("refresh-replies").checked, max_replies: Number($("refresh-reply-limit").value) }));
  $("comment-limit").addEventListener("change", updateButtons);
  $("detail-capture").addEventListener("click", () => { if (state.detail) capture(state.detail); });
  $("close-detail").addEventListener("click", () => $("note-detail").close());
  $("note-detail").addEventListener("close", () => { state.detail = ""; state.detailRequest++; clearPreview(); });
  $("cancel-note-job").addEventListener("click", async () => {
    const job = state.status?.jobs[0];
    if (!job) return;
    try { await api(`/api/jobs/${job.id}/cancel`, { method: "POST" }); await poll(); }
    catch (error) { toast(error.message); }
  });
  $("local-filter").addEventListener("input", () => { clearTimeout(filterTimer); filterTimer = setTimeout(() => { state.page = 1; loadList(); }, 250); });
  $("notes-prev").addEventListener("click", () => { state.page--; loadList(); });
  $("notes-next").addEventListener("click", () => { state.page++; loadList(); });
  $("export-notes-csv").addEventListener("click", () => download("csv"));
  $("export-notes-json").addEventListener("click", () => download("json"));
  if (commentsView) {
    document.body.classList.add("comments-view");
    $("page-title").textContent = "评论管理";
    $("page-description").textContent = "把分散的讨论收集到一起，搜索、阅读，保留有用的反馈。";
    $("list-title").textContent = "已保存评论与回复";
    $("local-filter").placeholder = "搜索评论文字或昵称";
    $("comment-scope").hidden = !selectedNote;
    $("comment-refresh").hidden = !selectedNote;
    $("all-comments").hidden = !selectedNote;
    $("comment-level").hidden = false;
    $("note-monitor-panel").hidden = !selectedNote;
    $("note-monitors-panel").hidden = false;
  }
  $(commentsView ? "nav-comments" : "nav-notes").classList.add("active");
  $(commentsView ? "nav-comments" : "nav-notes").setAttribute("aria-current", "page");
  document.title = `${commentsView ? "评论管理" : "笔记采集"} · 小红书工作台`;
  (async () => {
    try {
      const response = await fetch("/api/bootstrap", { cache: "no-store" });
      state.token = (await response.json()).token;
      await poll();
    } catch (error) { toast("无法连接本地服务，请刷新页面。"); }
  })();
})();
