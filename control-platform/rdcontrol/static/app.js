"use strict";
const apiBase = new URL("../", document.currentScript.src);
const $ = (id) => document.getElementById(id);
let csrf = "", setupRequired = false, currentPage = "overview", snapshot = null, refreshTimer;
const names = {overview:"概览",devices:"设备审批",users:"账号管理",audit:"活动记录",rustdesk:"RustDesk"};
const events = {platform_initialized:"完成平台初始化",admin_login:"管理员登录",login_failed:"登录失败",user_created:"创建账号",user_enabled:"启用账号",user_disabled:"禁用账号",device_enrolled:"新设备申请",device_login:"客户端登录",device_logout:"客户端退出",device_approved:"批准设备",device_revoked:"撤销设备",refresh_reuse_detected:"检测到旧凭证重复使用",upstream_unavailable:"RustDesk 后端不可用"};
const errors = {"Invalid username or password":"账号或密码不正确，或账号已停用。","Invalid setup code":"初始化代码不正确。","Too many attempts; try again later":"尝试次数过多，请稍后再试。","Username already exists":"此账号已经存在。","Password must contain 12–256 characters":"密码长度必须为 12–256 个字符。","Invalid username":"账号请使用 3–40 位字母、数字、点、下划线或横线。"};
const timeText = (t) => t ? new Date(t * 1000).toLocaleString("zh-CN", {hour12:false}) : "—";
function node(tag, text, className) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if(className) el.className = className; return el; }
function empty(parent, title, detail="") { parent.replaceChildren(); const box = node("div",undefined,"empty"); box.append(node("strong", title),node("span", detail)); parent.append(box); }
function badge(status) { return node("span", {approved:"已批准",pending:"待审批",revoked:"已撤销"}[status] || status, "badge " + status); }
async function ask(message) {
  const dialog=$("confirm-dialog");dialog.returnValue="cancel";$("confirm-message").textContent=message;dialog.showModal();
  return new Promise(resolve=>{dialog.addEventListener("close",()=>resolve(dialog.returnValue==="confirm"),{once:true});});
}
function toast(message) { $("notice").textContent=message; $("notice").hidden=false; setTimeout(()=>{$("notice").hidden=true;},7000); }
async function api(path, body) {
  const options={credentials:"same-origin",headers:{"Accept":"application/json"}};
  if(body !== undefined){ options.method="POST"; options.headers={"Accept":"application/json","Content-Type":"application/json","X-CSRF-Token":csrf}; options.body=JSON.stringify(body); }
  const res=await fetch(new URL(path.replace(/^\/+/, ""),apiBase),options); const data=await res.json();
  if(!res.ok) { if(res.status===401 && !$("console").hidden){showAuth();} throw new Error(errors[data.error] || data.error || `请求失败 (${res.status})`); }
  return data;
}
function showAuth() { clearInterval(refreshTimer); $("auth").hidden=false; $("console").hidden=true; csrf=""; }
function showConsole(username) { $("auth").hidden=true; $("console").hidden=false; $("admin-name").textContent=username; clearInterval(refreshTimer); refreshTimer=setInterval(()=>{if(!document.hidden) load().catch(()=>{});},10000); }
function page(name) {
  currentPage=name;
  document.querySelectorAll(".page").forEach(el=>el.hidden=el.id!=="page-"+name);
  document.querySelectorAll("nav [data-page]").forEach(el=>el.classList.toggle("active",el.dataset.page===name));
  $("page-title").textContent=name==="overview" ? "工作空间概览" : names[name];
  $("breadcrumb-page").textContent=names[name];
}
function table(parent, headings, rows, blank) {
  if(!rows.length){empty(parent,blank,"新的记录会显示在这里。");return;}
  const wrap=node("div",undefined,"table-wrap"), t=node("table"), head=node("thead"), tr=node("tr"), body=node("tbody");
  headings.forEach(x=>tr.append(node("th",x)));head.append(tr);t.append(head);
  rows.forEach(row=>{const r=node("tr");row.forEach(value=>{const cell=node("td");cell.append(value instanceof Node ? value : node("span",String(value)));r.append(cell);});body.append(r);});
  t.append(body);wrap.append(t);parent.replaceChildren(wrap);
}
function action(text, fn, danger=false) { const b=node("button",text,"text-button"+(danger?" danger":""));b.type="button";b.addEventListener("click",async()=>{b.disabled=true;try{await fn();await load();}catch(e){toast(e.message);}finally{b.disabled=false;}});return b; }
function label(main, detail) { const box=node("div");box.append(node("span",main),node("small",detail));return box; }
function auditRows(items) { return items.map(x=>[timeText(x.time),events[x.event] || x.event,x.username || "—",x.ip || "—",x.device_id ? x.device_id.slice(0,8) : "—"]); }
function render() {
  const {users,devices,audit,connections,active_transports}=snapshot;
  const pending=devices.filter(x=>x.status==="pending");
  $("metric-users").textContent=users.filter(x=>x.enabled).length;
  $("metric-pending").textContent=pending.length;
  $("metric-approved").textContent=devices.filter(x=>x.status==="approved").length;
  $("metric-transports").textContent=active_transports;
  $("pending-nav").textContent=pending.length || "";
  const pendingBox=$("overview-pending");pendingBox.replaceChildren();
  if(!pending.length) empty(pendingBox,"暂无待审批设备","新设备登录后，会在这里等待你的批准。");
  else pending.slice(0,4).forEach(d=>{const row=node("div",undefined,"pending-item");row.append(label(d.name,`${d.username} · ${d.platform}`),action("查看申请",()=>page("devices")));pendingBox.append(row);});
  const recent=$("recent-audit");recent.replaceChildren();
  if(!audit.length) empty(recent,"暂无活动记录");
  else audit.slice(0,5).forEach(a=>{const row=node("div",undefined,"activity");row.append(node("span",undefined,"dot"),node("span",(a.username ? a.username+" · " : "")+(events[a.event]||a.event)),node("time",timeText(a.time)));recent.append(row);});
  const shown=devices.filter(d=>$("device-filter").value==="all" || d.status===$("device-filter").value);
  table($("device-table"),["设备","所属账号","状态","最近出现","来源 IP","操作"],shown.map(d=>{
    const buttons=node("div");
    if(d.status!=="approved")buttons.append(action(d.status==="revoked"?"重新批准":"批准",async()=>{if(await ask(`批准设备“${d.name}”？批准后它可使用 RustDesk 服务。`))await api(`/api/admin/devices/${d.id}/decision`,{status:"approved"});}));
    if(d.status!=="revoked")buttons.append(action("撤销",async()=>{if(await ask(`撤销“${d.name}”的访问权限？当前通道也将关闭。`))await api(`/api/admin/devices/${d.id}/decision`,{status:"revoked"});},true));
    return [label(d.name,d.platform+" · "+d.id.slice(0,8)),d.username,badge(d.status),timeText(d.last_seen),d.last_ip || "—",buttons];
  }),"暂无符合条件的设备");
  table($("user-table"),["账号","角色","状态","创建时间","操作"],users.map(u=>[
    u.username,u.role==="admin"?"管理员":"成员",node("span",u.enabled?"已启用":"已停用","badge "+(u.enabled?"approved":"revoked")),timeText(u.created),
    u.role==="admin" ? "—" : action(u.enabled?"停用":"启用",async()=>{if(!u.enabled || await ask(`停用账号“${u.username}”并撤销其会话？`))await api(`/api/admin/users/${u.id}/status`,{enabled:!u.enabled});},!!u.enabled)
  ]),"暂无账号");
  table($("audit-table"),["时间","事件","操作账号","来源 IP","设备"],auditRows(audit),"暂无活动记录");
  table($("connection-table"),["设备 / 账号","通道类型","开始时间","结束时间","传输量","状态"],connections.map(c=>[
    label(c.name,c.username),c.kind==="relay"?"中继":"信令",timeText(c.started),timeText(c.ended),((c.bytes_up+c.bytes_down)/1024).toFixed(1)+" KB",
    c.outcome==="open"?"通道已建立":c.outcome==="interrupted_unknown"?"服务中断，结束时间未知":"通道已关闭"
  ]),"暂无连接通道");
  $("updated-at").textContent="自动刷新";
  $("footer-time").textContent=new Date().toLocaleTimeString("zh-CN",{hour12:false});
}
async function load(){snapshot=await api("/api/admin/overview");render();}
$("login-form").addEventListener("submit",async e=>{
  e.preventDefault();$("login-error").textContent="";$("login-button").disabled=true;
  try{
    const body={username:$("username").value.trim(),password:$("password").value};
    if(setupRequired){await api("/api/setup",{...body,token:$("setup-code").value.trim()});setupRequired=false;$("setup-code").value="";$("setup-code-label").hidden=true;}
    const result=await api("/api/admin/login",body);csrf=result.csrf;$("password").value="";showConsole(result.username);await load();
  }catch(err){$("login-error").textContent=err.message;}finally{$("login-button").disabled=false;}
});
document.querySelectorAll("[data-page]").forEach(b=>b.addEventListener("click",()=>page(b.dataset.page)));
$("refresh").addEventListener("click",()=>load().catch(e=>toast(e.message)));
$("logout").addEventListener("click",async()=>{try{await api("/api/admin/logout",{});}catch{}showAuth();});
$("device-filter").addEventListener("change",()=>{if(snapshot)render();});
$("new-user").addEventListener("click",()=>{$("user-error").textContent="";$("user-dialog").showModal();});
$("close-dialog").addEventListener("click",()=>$("user-dialog").close());
$("user-form").addEventListener("submit",async e=>{
  e.preventDefault();const form=e.currentTarget,button=form.querySelector("button[type=submit]");button.disabled=true;$("user-error").textContent="";
  try{const values=new FormData(form);await api("/api/admin/users",{username:values.get("username"),password:values.get("password")});form.reset();$("user-dialog").close();toast("账号已创建。新设备登录后仍需审批。");await load();}
  catch(err){$("user-error").textContent=err.message;}finally{button.disabled=false;}
});
(async()=>{
  try{
    const meta=await api("/api/meta");setupRequired=meta.setup_required;
    if(setupRequired){$("auth-title").textContent="初始化控制中心";$("auth-hint").textContent="使用服务器上的一次性代码创建管理员。";$("setup-code-label").hidden=false;$("login-button").textContent="创建管理员并登录";$("password").autocomplete="new-password";return;}
    try{const session=await api("/api/admin/session");csrf=session.csrf;showConsole(session.username);await load();}catch{showAuth();}
  }catch(e){$("login-error").textContent="暂时无法连接管理服务，请稍后刷新。";}
})();
