"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const labels = {draft:"草稿",failed:"提交前已停止",queued:"排队中",preparing:"准备中",submitting:"提交中",uncertain:"待核对",published:"已核实提交"};
  const editable = draft => !draft || ["draft","failed"].includes(draft.state);
  const state = {token:"", status:null, draft:null, job:null, uploading:false, acting:false, previews:new Map(), qrURL:"", qrKey:"", items:[]};
  let timer, toastTimer;
  let historyKey="", assetsKey="";
  function toast(message) { $("toast").textContent=message; $("toast").hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>{$("toast").hidden=true;},5000); }
  async function api(path, options={}) {
    const headers={"X-Local-Token":state.token,...options.headers};
    if(options.json) { headers["Content-Type"]="application/json"; options.body=JSON.stringify(options.json); delete options.json; }
    const response=await fetch(path,{...options,headers,cache:"no-store"});
    if(!response.ok) { const body=await response.json().catch(()=>({})); throw Error(typeof body.detail==="string"?body.detail:"操作未完成，请稍后重试。"); }
    return response;
  }
  async function json(path, options={}) { return (await api(path,options)).json(); }
  function releasePreviews() { state.previews.forEach(url=>URL.revokeObjectURL(url)); state.previews.clear(); }
  function formData() { return {kind:$("kind").value,title:$("title").value,body:$("body").value,tags:$("tags").value.split(/[,，]/).map(v=>v.trim()).filter(Boolean)}; }
  function counts() { $("title-count").textContent=`${Array.from($("title").value).length} / 20`; $("body-count").textContent=`${Array.from($("body").value).length} / 1000 字（不含话题）`; }
  function kindHint() {
    const video=$("kind").value==="video";
    document.querySelector('label[for="media-files"]').textContent=video?"视频素材":"图片素材";
    $("media-files").accept=video?".mp4,.mov":".jpg,.jpeg,.png,.webp"; $("media-files").multiple=!video;
    $("media-hint").textContent=video?"上传 1 个 MP4 或 MOV 视频，不超过 500 MB。选择后保存到当前草稿。":"最多 9 张 JPEG、PNG 或 WebP，每张不超过 20 MB。选择后保存到当前草稿。";
  }
  function controls() {
    const busy=Boolean(state.status?.busy), locked=!editable(state.draft), acting=state.acting||state.uploading;
    $("draft-fields").disabled=locked||acting;
    $("new-draft").disabled=acting;
    $("login").disabled=busy||acting||!state.status?.environment_ready;
    $("check-auth").disabled=busy||acting||!state.status?.environment_ready||!state.status?.credential_saved;
    $("submit-publish").disabled=busy||acting||locked||!state.draft?.assets.length||!state.status?.environment_ready||!state.status?.credential_saved;
    $("verify-result").hidden=state.draft?.state!=="uncertain";
    $("verify-result").disabled=busy||acting||!state.status?.credential_saved||!state.status?.environment_ready;
    $("draft-state").textContent=state.draft?labels[state.draft.state]:"新草稿";
    $("draft-state").className=state.draft?.state==="published"?"badge":"badge neutral";
    $("draft-message").textContent=state.draft?.message||"保存草稿并上传素材后，即可提交。";
  }
  async function saveDraft() {
    if(!$("draft-form").reportValidity()) throw Error("请先填写标题。");
    const target=state.draft?`/api/publish/drafts/${state.draft.id}`:"/api/publish/drafts";
    state.draft=await json(target,{method:state.draft?"PUT":"POST",json:formData()}); controls();
    $("title").value=state.draft.title; $("body").value=state.draft.body; $("tags").value=state.draft.tags.join("，"); counts();
    return state.draft;
  }
  function renderHistory() {
    const key=JSON.stringify([state.items,state.draft?.id,state.uploading,state.acting]);
    if(key===historyKey)return;
    historyKey=key;
    const focused=document.activeElement?.dataset?.draftId;
    $("history").replaceChildren(); $("history-empty").hidden=state.items.length>0;
    for(const item of state.items) {
      const button=document.createElement("button"); button.type="button"; button.className="publish-history-item"+(state.draft?.id===item.id?" selected":"");
      button.dataset.draftId=item.id;
      const title=document.createElement("strong"); title.textContent=item.title;
      const badge=document.createElement("span"); badge.className=item.state==="published"?"badge":"badge neutral"; badge.textContent=labels[item.state];
      const meta=document.createElement("small"); meta.textContent=`${item.kind==="video"?"视频":"图文"} · ${new Date(item.updated_at*1000).toLocaleString("zh-CN")}`;
      button.append(title,badge,meta); button.disabled=state.uploading||state.acting;
      button.addEventListener("click",()=>act(()=>loadDraft(item.id))); $("history").append(button);
    }
    if(focused)$("history").querySelector(`[data-draft-id="${focused}"]`)?.focus({preventScroll:true});
  }
  function renderAssets() {
    const key=JSON.stringify([state.draft?.id,state.draft?.state,state.draft?.assets,Array.from(state.previews.keys())]);
    if(key===assetsKey)return;
    assetsKey=key;
    $("assets").replaceChildren();
    for(const asset of state.draft?.assets||[]) {
      const row=document.createElement("div"); row.className="publish-asset";
      const preview=state.previews.get(asset.id);
      if(preview&&state.draft.kind==="image") { const image=document.createElement("img"); image.src=preview; image.alt=asset.name; row.append(image); }
      const details=document.createElement("div"), name=document.createElement("p"), size=document.createElement("small"); name.textContent=asset.name; size.textContent=`${(asset.size/1024/1024).toFixed(2)} MB`; details.append(name,size); row.append(details);
      const view=document.createElement("button"); view.type="button"; view.className="text-button"; view.textContent="查看素材";
      view.addEventListener("click",()=>act(async()=>{ const response=await api(`/api/publish/drafts/${state.draft.id}/assets/${asset.id}`); const url=URL.createObjectURL(await response.blob()); const a=document.createElement("a"); a.href=url; a.download=asset.name; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); })); row.append(view);
      if(editable(state.draft)) { const remove=document.createElement("button"); remove.type="button"; remove.className="text-button"; remove.textContent="移除"; remove.disabled=state.uploading; remove.addEventListener("click",()=>act(async()=>{state.draft=await json(`/api/publish/drafts/${state.draft.id}/assets/${asset.id}`,{method:"DELETE"}); const url=state.previews.get(asset.id); if(url)URL.revokeObjectURL(url); state.previews.delete(asset.id); renderAssets(); controls(); await refresh();})); row.append(remove); }
      $("assets").append(row);
    }
  }
  async function loadDraft(id) {
    if(editable(state.draft)&&formDirty()&&!confirm("当前修改尚未保存，要切换草稿吗？"))return;
    releasePreviews(); state.draft=await json(`/api/publish/drafts/${id}`);
    $("kind").value=state.draft.kind; $("title").value=state.draft.title; $("body").value=state.draft.body; $("tags").value=state.draft.tags.join("，"); $("media-files").value="";
    kindHint(); counts(); renderAssets(); controls(); renderHistory();
  }
  function formDirty() { const form=formData(); return state.draft?JSON.stringify(form)!==JSON.stringify({kind:state.draft.kind,title:state.draft.title,body:state.draft.body,tags:state.draft.tags}):Boolean(form.title||form.body||form.tags.length); }
  async function refresh() {
    const [status,history,overall]=await Promise.all([json("/api/publish/status"),json("/api/publish/drafts"),json("/api/status")]);
    state.status=status; state.items=history.items; const previous=state.job;
    state.job=overall.jobs.find(job=>job.kind.startsWith("publish_"))||null;
    $("setup-warning").hidden=status.environment_ready;
    $("connection-heading").textContent="发布账号"+(status.account_label?" · "+status.account_label:"");
    const recent=status.auth_state==="valid"&&status.checked_at&&Date.now()/1000-status.checked_at<900;
    $("auth-badge").textContent=recent?"发布登录已验证":status.auth_state==="invalid"?"发布登录已失效":status.credential_saved?"凭据已保存 · 请检查登录":"发布账号未登录";
    $("auth-badge").className=recent?"badge":"badge neutral";
    $("checked-at").textContent=status.checked_at?`最近验证：${new Date(status.checked_at*1000).toLocaleString("zh-CN")}；发布前会再次检查。`:"尚未检查发布登录状态。";
    const job=state.job; $("job-panel").hidden=!job;
    if(job) { $("job-title").textContent=job.title; $("job-message").textContent=job.message; $("cancel-job").hidden=job.status!=="running"; }
    const waiting=job?.kind==="publish_login"&&job.status==="running";
    if(waiting) {
      if(state.qrKey!==job.id) { state.qrKey=job.id; if(state.qrURL)URL.revokeObjectURL(state.qrURL); state.qrURL=""; }
      if(!state.qrURL) { try { const response=await api("/api/publish/qr"); state.qrURL=URL.createObjectURL(await response.blob()); $("login-qr").src=state.qrURL; } catch {} }
    } else if(state.qrURL) { URL.revokeObjectURL(state.qrURL); state.qrURL=""; $("login-qr").removeAttribute("src"); }
    $("login-panel").hidden=!waiting||!state.qrURL;
    if(state.draft&&!state.uploading) {
      const latest=history.items.find(item=>item.id===state.draft.id);
      if(latest&&(!editable(latest)||(!state.acting&&latest.state!==state.draft.state))) { state.draft=latest; renderAssets(); }
    }
    if(previous?.status==="running"&&job?.id===previous.id&&job.status!=="running") toast(job.message);
    controls(); renderHistory();
  }
  async function act(callback) { if(state.acting)return; state.acting=true; controls(); try{await callback();}catch(error){toast(error.message);}finally{state.acting=false;controls();renderHistory();} }
  $("draft-form").addEventListener("submit",event=>{event.preventDefault();act(async()=>{await saveDraft();toast("草稿已保存。");await refresh();});});
  $("kind").addEventListener("change",kindHint); $("title").addEventListener("input",counts); $("body").addEventListener("input",counts);
  $("new-draft").addEventListener("click",()=>{if(formDirty()&&!confirm("当前修改尚未保存，要新建草稿吗？"))return;releasePreviews();state.draft=null;$("draft-form").reset();kindHint();counts();renderAssets();controls();renderHistory();});
  $("media-files").addEventListener("change",()=>act(async()=>{
    const files=Array.from($("media-files").files); if(!files.length)return;
    const kind=$("kind").value, maxCount=kind==="video"?1:9, maxSize=(kind==="video"?500:20)*1024*1024;
    if(files.length+(state.draft?.assets.length||0)>maxCount||files.some(file=>file.size>maxSize))throw Error("素材数量或大小超出限制，请重新选择。");
    state.uploading=true; controls();
    try{await saveDraft();for(const file of files){const draft=await json(`/api/publish/drafts/${state.draft.id}/assets?name=${encodeURIComponent(file.name)}`,{method:"POST",body:file,headers:{"Content-Type":"application/octet-stream"}}); const asset=draft.assets[draft.assets.length-1]; state.previews.set(asset.id,URL.createObjectURL(file));state.draft=draft;renderAssets();} toast("素材已保存到草稿。");}
    finally{state.uploading=false;$("media-files").value="";renderAssets();controls();await refresh();}
  }));
  $("login").addEventListener("click",()=>act(async()=>{await json("/api/publish/login",{method:"POST"});await refresh();}));
  $("check-auth").addEventListener("click",()=>act(async()=>{await json("/api/publish/check",{method:"POST"});await refresh();}));
  $("refresh").addEventListener("click",()=>act(refresh));
  $("submit-publish").addEventListener("click",()=>act(async()=>{await saveDraft();$("confirm-summary").textContent=`「${state.draft.title}」 · ${state.draft.kind==="video"?"视频":"图文"} · ${state.draft.assets.length} 个素材 · ${state.status.account_label||"当前发布账号"}`;$("confirm-dialog").showModal();}));
  $("confirm-cancel").addEventListener("click",()=>$("confirm-dialog").close());
  $("confirm-send").addEventListener("click",()=>act(async()=>{$("confirm-dialog").close();await json(`/api/publish/drafts/${state.draft.id}/submit`,{method:"POST",json:{confirmed:true,version:state.draft.version}});await refresh();}));
  $("verify-result").addEventListener("click",()=>act(async()=>{await json(`/api/publish/drafts/${state.draft.id}/verify`,{method:"POST"});await refresh();}));
  $("cancel-job").addEventListener("click",()=>act(async()=>{if(!confirm("停止当前任务？已经开始提交的笔记仍可能发布成功，之后需要核对结果。"))return;await json(`/api/jobs/${state.job.id}/cancel`,{method:"POST"});await refresh();}));
  window.addEventListener("beforeunload",event=>{if(state.uploading||formDirty()){event.preventDefault();event.returnValue="";}});
  window.addEventListener("pagehide",()=>{clearTimeout(timer);releasePreviews();if(state.qrURL)URL.revokeObjectURL(state.qrURL);});
  async function poll(){try{await refresh();}catch{}finally{timer=setTimeout(poll,2000);}}
  (async()=>{try{const response=await fetch("/api/bootstrap",{cache:"no-store"});state.token=(await response.json()).token;kindHint();counts();await poll();}catch{toast("无法连接本地工作台，请刷新页面重试。");}})();
})();
