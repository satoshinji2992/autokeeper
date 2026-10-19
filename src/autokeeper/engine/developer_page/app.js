"use strict";
const $ = id => document.getElementById(id);
const pretty = data => JSON.stringify(data, null, 2);
let cursor = 0, count = 0, loading = false, direction = "older";
async function api(path, options) {
  const response = await fetch(`api/${path}`, options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
for (const button of document.querySelectorAll("[data-tab]")) {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-tab],main>section").forEach(el => el.classList.remove("active"));
    button.classList.add("active"); $(button.dataset.tab).classList.add("active");
  });
}
function metric(label, value) {
  const div = document.createElement("div"); div.className = "metric";
  const title = document.createElement("span"); title.textContent = label;
  const text = document.createElement("b"); text.textContent = String(value);
  div.append(title, text); return div;
}
function rowsTable(headings, rows) {
  const table = document.createElement("table"), head = document.createElement("tr");
  for (const label of headings) { const th = document.createElement("th"); th.textContent = label; head.append(th); }
  table.append(head);
  for (const values of rows) {
    const row = document.createElement("tr");
    for (const value of values) { const cell = document.createElement("td"); cell.textContent = String(value); row.append(cell); }
    table.append(row);
  }
  return table;
}
function empty(text) { const p = document.createElement("p"); p.textContent = text; return p; }
function renderActivity(data) {
  const stage = {started:"请求已发送", attempt:"正在处理", retry:"等待重试", repair:"修复输出格式"};
  const calls = data.backstage.active_calls.map(c => [c.session || c.agent, c.model,
    `${stage[c.phase] || c.phase}${c.attempt ? ` · 第 ${c.attempt} 次` : ""}${c.delay ? ` · ${c.delay.toFixed(1)} 秒` : ""}`]);
  const tasks = data.backstage.active_tasks.map(t => [t.name, "后台任务", "进行中"]);
  $("activity").replaceChildren(calls.length || tasks.length ? rowsTable(["任务／会话", "模型", "进度"], calls.concat(tasks)) : empty("当前没有进行中的请求或任务。"));
  const plans = data.backstage.director.map(p => [p.kind, `第 ${p.turn} 轮`, p.cancelled ? "已取消" : p.done ? "已生成，等待核对与提交" : "正在准备"]);
  $("director-jobs").replaceChildren(plans.length ? rowsTable(["内容", "发起时间", "状态"], plans) : empty("当前没有待提交的导演内容。"));
  const states = {pending:"准备中", ready:"已就绪", failed:"失败"};
  const images = Object.entries(data.state.visuals || {}).map(([key, v]) => [v.brief || key, `${states[v.status] || v.status}${v.error ? ` · ${v.error}` : ""}`]);
  const pluginErrors = Object.entries(data.state.plugin_errors || {}).map(([key, error]) => [key, error]);
  $("asset-jobs").replaceChildren(images.length || pluginErrors.length ? rowsTable(["内容", "状态"], images.concat(pluginErrors)) : empty("当前没有待处理图像或规则错误。"));
}
function renderActors(world) {
  const table = document.createElement("table");
  const head = document.createElement("tr");
  for (const label of ["人物", "实际位置／状态", "目标与计划"]) { const th = document.createElement("th"); th.textContent = label; head.append(th); }
  table.append(head);
  for (const [id, entity] of Object.entries(world.entities || {})) {
    if (entity.kind !== "npc") continue;
    const row = document.createElement("tr"), agent = (world.agents || {})[id] || {};
    const place = (world.entities || {})[entity.at];
    const values = [ `${entity.name} [${id}]`, `${place ? place.name : entity.at || "—"} · ${entity.status || "正常"}${agent.mood ? `\n情绪：${agent.mood}` : ""}`,
      `${(agent.goals || []).join("；") || "未设独立目标"}\n${(agent.plan || []).map(s => `${s.done ? "已完成" : "待执行"} ${s.id}${s.when ? ` [${s.when}]` : ""}${s.note ? `：${s.note}` : ""}`).join("\n")}${entity.following ? `\n同行：${entity.following} → ${entity.follow_to || "持续跟随"}` : ""}` ];
    for (const value of values) { const cell = document.createElement("td"); cell.textContent = value; row.append(cell); }
    table.append(row);
  }
  $("actors").replaceChildren(table);
}
function renderAnalysis(data) {
  $("ask").disabled = data.working;
  $("analysis-status").textContent = data.error || (data.working ? "正在排查" : data.answer ? "排查完成" : "尚未提交排查");
  $("analysis-answer").textContent = data.answer || "";
  const scope = data.evidence_scope;
  $("analysis-scope").textContent = scope ? `${scope.selection} / 匹配 ${scope.matching_entries} 条 / 提交 ${scope.submitted_entries} 条\n记录：${scope.submitted_cursors.map(n => `#${n}`).join(" · ")}\n${scope.notice}` : "";
}
async function refresh() {
  try {
    const data = await api("snapshot"), view = data.overview;
    $("status").textContent = `${view.module} / 第 ${view.turn} 轮 / ${view.clock}`;
    const place = (data.module.locations || []).find(loc => loc.id === view.location);
    $("metrics").replaceChildren(metric("当前地点", place ? place.name : view.location), metric("游戏回合", view.turn),
      metric("进行中的模型", data.backstage.active_calls.length), metric("后台任务", data.backstage.active_tasks.length));
    $("backstage").textContent = pretty(data.backstage); $("state").textContent = pretty(data.state); renderActivity(data);
    $("world-json").textContent = pretty(data.world); $("module-json").textContent = pretty(data.module);
    $("sessions-json").textContent = pretty(data.sessions); renderActors(data.world);
    const audit = data.maintenance, counts = audit.counts;
    $("maintenance-summary").textContent = `错误 ${counts.error} · 待核对 ${counts.warning} · 状态事项 ${counts.info}。${audit.scope}`;
    $("maintenance-report").textContent = pretty(audit);
    $("capture-note").textContent = `${view.capture_note}\n${view.record_file}\n${view.record_error || ""}`;
    renderAnalysis(data.analysis); $("connection").textContent = `只读检查 / 已更新 ${new Date().toLocaleTimeString()}`;
  } catch (error) { $("connection").textContent = `连接中断：${error.message}`; }
}
async function records(reset = false) {
  if (loading) return;
  loading = true; $("more-records").disabled = true;
  if (reset) { cursor = 0; count = 0; $("record-list").replaceChildren(); }
  try {
    const data = await api(`records?after=${cursor}&limit=80&direction=${direction}&kind=${encodeURIComponent($("record-kind").value)}`);
    for (const row of data.records) {
      const details = document.createElement("details"), summary = document.createElement("summary"), body = document.createElement("pre");
      const event = row.data || {};
      summary.textContent = `#${row.cursor}  ${row.at}  轮 ${row.turn ?? "—"}  ${row.kind}  ${event.session || event.name || event.type || ""}  ${event.error || event.status || event.phase || ""}`;
      const inspect = document.createElement("button"); inspect.textContent = "排查此记录";
      inspect.addEventListener("click", () => {
        $("question").value = `请核对第 ${row.turn ?? 0} 轮的记录 #${row.cursor}，检查输入、世界操作和最终叙述是否一致。`;
        $("from-turn").value = ""; $("to-turn").value = "";
        document.querySelector('[data-tab="analysis"]').click(); $("question").focus();
      });
      body.textContent = pretty(row); details.append(summary, body, inspect); $("record-list").append(details);
    }
    cursor = data.cursor; count += data.records.length; $("record-count").textContent = `已读取 ${count} 条 / 游标 ${cursor}`;
  } catch (error) { $("record-count").textContent = error.message; }
  finally { loading = false; $("more-records").disabled = false; }
}
$("reload-records").addEventListener("click", () => { direction = "forward"; $("more-records").textContent = "读取下一页"; records(true); });
$("latest-records").addEventListener("click", () => { direction = "older"; $("more-records").textContent = "读取更早记录"; records(true); });
$("record-kind").addEventListener("change", () => records(true));
$("more-records").addEventListener("click", () => records());
$("analysis-form").addEventListener("submit", async event => {
  event.preventDefault(); $("ask").disabled = true;
  try {
    const body = {question: $("question").value, from_turn: $("from-turn").value ? Number($("from-turn").value) : null,
      to_turn: $("to-turn").value ? Number($("to-turn").value) : null};
    renderAnalysis(await api("analysis", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)}));
  } catch (error) { $("analysis-status").textContent = error.message; $("ask").disabled = false; }
});
refresh(); records(); setInterval(refresh, 2500);
