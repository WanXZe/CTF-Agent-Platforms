const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const encode = value => encodeURIComponent(String(value));
const number = value => Number(value || 0).toLocaleString('zh-CN');
const labels = {challenges:'赛题库',directions:'方向与模型',models:'模型连接',usage:'调用用量'};
const statuses = {idle:'未开始',running:'分析中',pausing:'暂停中',paused:'已暂停',cancelling:'取消中',cancelled:'已取消',failed:'失败',needs_human:'等待确认',success:'已完成',interrupted:'已中断',cleanup_failed:'清理失败'};
const activeStates = new Set(['running','pausing','paused','cancelling']);
const state = {platform:'',platforms:[],models:[],globalModel:'',directions:[],challenges:[],sessions:[],view:'challenges',detail:null,editor:null,query:'',category:'',solved:'',revision:0,logRevision:0,busy:new Set()};
let pollTimer;

async function api(path, {method='GET',body,platform=state.platform}={}) {
  const headers = platform ? {'X-Platform-Id':platform} : {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path,{method,headers,body:body !== undefined ? JSON.stringify(body) : undefined});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : payload.message || `请求失败（HTTP ${response.status}）`);
  return payload;
}
function notify(message, error=false) {
  const node = document.createElement('div'); node.className = 'notification' + (error ? ' error' : '');
  node.textContent = message; $('notifications').append(node); setTimeout(() => node.remove(),6000);
}
async function exclusive(key,button,operation) {
  if (state.busy.has(key)) return;
  state.busy.add(key); if (button) button.disabled = true;
  try { await operation(); } catch (error) { notify(error.message,true); }
  finally { state.busy.delete(key); if (button?.isConnected) button.disabled = false; syncSolveControls(); }
}
function pageTitle(title,caption,action='') {
  return `<div class="page-title"><div><span class="eyebrow">CTF RESEARCH / ${escapeHTML(state.view.toUpperCase())}</span><h1>${title}</h1><p class="subtle">${caption}</p></div>${action}</div>`;
}
function metric(label,value,symbol) { return `<div class="metric"><div><small>${label}</small><strong>${number(value)}</strong></div><span class="metric-icon" aria-hidden="true">${symbol}</span></div>`; }
function modelOptions(selected='',automatic='') {
  return `${automatic ? `<option value="">${escapeHTML(automatic)}</option>` : ''}${state.models.map(model => `<option value="${escapeHTML(model.name)}" ${model.name === selected ? 'selected' : ''}>${escapeHTML(model.name)}</option>`).join('')}`;
}
function sessionFor(id) { return state.sessions.find(session => session.platform_id === state.platform && String(session.challenge_id) === String(id)); }
function stopPolling() { clearTimeout(pollTimer); state.logRevision++; }
function navigate(view) {
  stopPolling(); state.detail = null; state.view = view;
  document.querySelectorAll('[data-view]').forEach(button => button.classList.toggle('selected',button.dataset.view === view));
  $('locationLabel').textContent = labels[view]; render();
}
async function loadWorkspace() {
  const revision = ++state.revision, platform = state.platform;
  const [challenges,directions,sessions,models] = await Promise.all([
    api('/api/challenges',{platform}),api('/api/category-models',{platform}),
    api(`/api/solve-sessions?platform_id=${encode(platform)}`,{platform}),api('/api/models',{platform:''})
  ]);
  if (revision !== state.revision || platform !== state.platform) return;
  Object.assign(state,{challenges:challenges.data,directions:directions.data,sessions:sessions.data,models:models.data,globalModel:models.default});
  if (!state.detail) render();
}
function render() {
  if (!state.platform) { $('workspace').innerHTML = '<div class="empty"><strong>连接一个题库</strong>添加平台后即可查看赛题。</div>'; return; }
  if (state.view === 'challenges') renderChallenges();
  if (state.view === 'directions') renderDirections();
  if (state.view === 'models') renderModels();
  if (state.view === 'usage') renderUsage().catch(error => notify(error.message,true));
}
function renderChallenges() {
  const categories = [...new Set(state.challenges.map(item => item.model_category))].sort();
  $('workspace').innerHTML = pageTitle('赛题研究，从这里开始。','选择题目，查看附件与历史记录，启动新的分析。','<button class="secondary" data-action="refresh">↻ 刷新题库</button>') +
    `<div class="metrics">${metric('题库中的赛题',state.challenges.length,'▦')}${metric('已解出的题目',state.challenges.filter(item => item.solved).length,'✓')}${metric('正在进行的分析',state.sessions.filter(item => activeStates.has(item.status)).length,'↗')}${metric('可用模型连接',state.models.length,'◇')}</div>
    <section class="board"><div class="board-top"><h2>全部赛题</h2><small id="resultSummary"></small></div><div class="filters"><label class="search-box"><span aria-hidden="true">⌕</span><input id="questionSearch" type="search" value="${escapeHTML(state.query)}" placeholder="搜索题目名称或编号" aria-label="搜索赛题"></label><select id="directionFilter" aria-label="筛选方向"><option value="">全部方向</option>${categories.map(category => `<option value="${escapeHTML(category)}" ${category === state.category ? 'selected' : ''}>${escapeHTML(category)}</option>`).join('')}</select><select id="solvedFilter" aria-label="筛选状态"><option value="">全部状态</option><option value="no" ${state.solved === 'no' ? 'selected' : ''}>未解出</option><option value="yes" ${state.solved === 'yes' ? 'selected' : ''}>已解出</option></select></div><table class="grid-table"><thead><tr><th>编号</th><th>题目</th><th>方向</th><th>方向默认模型</th><th>进度</th><th>操作</th></tr></thead><tbody id="questionRows"></tbody></table><div class="empty" id="noResults" hidden>没有符合筛选条件的赛题。</div></section>`;
  renderRows();
}
function renderRows() {
  if (!$('questionRows')) return;
  const query = state.query.trim().toLowerCase();
  const rows = state.challenges.filter(item => (!query || `${item.id} ${item.name}`.toLowerCase().includes(query)) && (!state.category || item.model_category === state.category) && (!state.solved || item.solved === (state.solved === 'yes')));
  $('resultSummary').textContent = `${rows.length} 道题目 / ${new Set(rows.map(item => item.model_category)).size} 个方向`;
  $('noResults').hidden = rows.length !== 0;
  $('questionRows').innerHTML = rows.map(item => {
    const session = sessionFor(item.id), status = session?.status || 'idle';
    return `<tr data-question="${escapeHTML(item.id)}"><td>${escapeHTML(item.id)}</td><td><button class="question-name" data-action="open-question" data-id="${escapeHTML(item.id)}">${escapeHTML(item.name)}</button><small class="row-caption">${number(item.value)} 分 · ${item.need_container ? '容器环境' : '附件分析'}</small></td><td><span class="tag">${escapeHTML(item.model_category)}</span></td><td>${escapeHTML(item.default_model)}<small class="row-caption">${item.model_source === 'category' ? '方向配置' : '跟随全局'}</small></td><td><span class="status" data-active="${activeStates.has(status)}">${item.solved ? '✓ 已解出' : escapeHTML(statuses[status] || status)}</span></td><td><button class="quiet" data-action="open-question" data-id="${escapeHTML(item.id)}">进入 ↗</button></td></tr>`;
  }).join('');
}
function renderDirections() {
  $('workspace').innerHTML = pageTitle('为每个方向选择模型。','方向默认配置适用于所有平台；同方向的赛题使用同一个默认模型。') +
    `<div class="intro-note"><span>没有单独配置的方向会跟随全局模型：<strong>${escapeHTML(state.globalModel)}</strong></span><button class="inline-link" data-view="models">管理模型连接 ↗</button></div><div class="card-grid">${state.directions.map(item => {
      const count = state.challenges.filter(question => question.model_category === item.category).length;
      return `<section class="direction-card" data-category="${escapeHTML(item.category)}"><div class="direction-top"><span class="direction-symbol">${escapeHTML(item.category.slice(0,2).toUpperCase())}</span><div class="direction-title"><h3>${escapeHTML(item.category)}</h3><small>当前平台 ${count} 道题目</small></div></div><label>默认解题模型<select aria-label="${escapeHTML(item.category)} 默认模型">${modelOptions(item.configured_default_model,`跟随全局 · ${state.globalModel}`)}</select></label><div class="card-footer"><small>当前：${escapeHTML(item.default_model)}</small><button class="secondary" data-action="save-direction" data-category="${escapeHTML(item.category)}">保存配置</button></div></section>`;
    }).join('')}</div>`;
}
function renderModels() {
  $('workspace').innerHTML = pageTitle('管理模型连接。','查看已有连接，随时修改模型名称、提供商、接口地址和密钥变量。','<button class="primary" data-action="new-model">＋ 添加模型</button>') +
    `<div class="card-grid">${state.models.map(model => `<section class="connection-card" data-model="${escapeHTML(model.name)}"><div class="connection-top"><h3>${escapeHTML(model.name)}</h3>${model.name === state.globalModel ? '<span class="tag">全局默认</span>' : '<span class="tag">已配置</span>'}</div><dl><div><dt>提供商</dt><dd>${escapeHTML(model.provider || 'custom')}</dd></div><div><dt>接口地址</dt><dd>${escapeHTML(model.base_url)}</dd></div><div><dt>密钥变量</dt><dd><code>${escapeHTML(model.api_key_env || '无需密钥')}</code></dd></div></dl><div class="card-footer"><button class="secondary" data-action="edit-model" data-name="${escapeHTML(model.name)}">编辑连接</button><button class="quiet" data-action="delete-model" data-name="${escapeHTML(model.name)}">删除</button></div></section>`).join('') || '<div class="empty">添加第一个模型连接后即可配置方向默认模型。</div>'}</div>`;
}
function editModel(name=null) {
  const model = state.models.find(item => item.name === name);
  state.editor = name; const form = $('connectionForm'); form.reset();
  for (const field of ['name','provider','base_url','api_key_env']) form.elements.namedItem(field).value = model?.[field] || '';
  $('modelEditorTitle').textContent = model ? '编辑模型连接' : '添加模型连接';
  $('modelEditorError').textContent = ''; $('modelEditor').showModal();
}
async function saveModel(form) {
  const original = state.editor, values = Object.fromEntries(new FormData(form));
  $('modelEditorError').textContent = '';
  try {
    await api(original ? `/api/models/${encode(original)}` : '/api/models',{method:original ? 'PUT' : 'POST',body:values,platform:''});
    $('modelEditor').close(); await loadWorkspace(); notify(original ? '连接信息已更新，方向引用已同步。' : '模型连接已添加。');
  } catch (error) { $('modelEditorError').textContent = error.message; }
}
async function renderUsage() {
  const revision = state.revision, platform = state.platform;
  $('workspace').innerHTML = pageTitle('每次调用，都有记录。','查看当前平台的题目用量和全局累计统计。') + '<div class="empty">正在加载调用统计…</div>';
  const result = await api(`/api/token-stats?platform_id=${encode(platform)}`,{platform});
  if (revision !== state.revision || state.view !== 'usage' || platform !== state.platform) return;
  const {total,challenges} = result.data;
  $('workspace').innerHTML = pageTitle('每次调用，都有记录。','下方展示当前平台的题目用量；汇总为全局累计统计。','<button class="secondary" data-action="refresh-usage">↻ 刷新统计</button>') +
    `<div class="metrics">${metric('累计模型调用',total.calls,'◇')}${metric('累计 Token',total.total_tokens,'▥')}${metric('输入 Token',total.prompt_tokens,'↗')}${metric('输出 Token',total.completion_tokens,'↙')}</div><section class="board usage-table-wrap"><table class="grid-table usage-table"><thead><tr><th>题目</th><th>模型</th><th>调用次数</th><th>输入</th><th>输出</th><th>Token 合计</th></tr></thead><tbody>${challenges.map(item => `<tr><td>#${escapeHTML(item.challenge_id)}</td><td>${escapeHTML(item.model || '—')}</td><td>${number(item.calls)}</td><td>${number(item.prompt_tokens)}</td><td>${number(item.completion_tokens)}</td><td>${number(item.total_tokens)}</td></tr>`).join('')}</tbody></table>${challenges.length ? '' : '<div class="empty">还没有模型调用记录。</div>'}</section>`;
}
async function openQuestion(id) {
  stopPolling(); const revision = ++state.logRevision, platform = state.platform;
  state.detail = {id:String(id),platform,status:'idle',kind:'solve',model:'',challenge:null,loading:false};
  $('workspace').innerHTML = '<div class="empty">正在加载题目与完整历史索引…</div>';
  const [challenge,log] = await Promise.all([api(`/api/challenges/${encode(id)}`,{platform}),api(`/api/solve-log/${encode(platform)}/${encode(id)}?limit=200`,{platform})]);
  if (revision !== state.logRevision || platform !== state.platform || !state.detail) return;
  Object.assign(state.detail,{challenge:challenge.data,status:log.data.status || 'idle'});
  if (activeStates.has(state.detail.status)) state.detail.model = log.data.model || '';
  $('locationLabel').textContent = '题目工作台'; renderQuestion(); updateLogs(log.data); schedulePoll();
}
function renderQuestion() {
  const detail = state.detail, question = detail.challenge;
  $('workspace').innerHTML = `<button class="back-link" data-action="back-questions">← 返回赛题库</button><div class="detail-heading"><span class="question-id">#${escapeHTML(question.id)}</span><h1>${escapeHTML(question.name)}</h1><span class="tag">${escapeHTML(question.model_category)}</span></div>
    <div class="detail-grid"><div class="question-pane"><section class="pane-section"><h3>题目内容</h3><pre class="description">${escapeHTML(question.description || '暂无题目描述。')}</pre><div class="files">${question.files.map(file => `<button data-action="attachment" data-filename="${escapeHTML(file.name)}">↓ ${escapeHTML(file.name)}</button>`).join('') || '<span class="subtle">无附件</span>'}</div></section>
    ${question.need_container ? `<section class="pane-section"><h3>题目环境</h3><div class="container-state" id="environmentInfo">${escapeHTML(question.connection_info || question.container_status || '未启动')}</div><div class="solve-actions"><button class="secondary" data-action="environment-start">启动环境</button><button class="secondary" data-action="environment-status">刷新状态</button><button class="quiet" data-action="environment-stop">停止环境</button></div></section>` : ''}
    <section class="pane-section"><h3>Agent 解题</h3><p class="subtle">${escapeHTML(question.model_category)} 方向默认：<strong id="directionDefaultLabel">${escapeHTML(question.default_model)}</strong></p><div class="solve-picker"><select id="runModel" aria-label="本轮解题模型">${modelOptions(detail.model,`使用方向默认 · ${question.default_model}`)}</select><button class="secondary" data-action="check-connection">检查</button></div><div class="connection-result" id="connectionResult" role="status"></div><div class="solve-actions"><button class="primary" data-action="start-solve">开始分析</button><button class="secondary" data-action="pause-solve">暂停</button><button class="secondary" data-action="resume-solve">继续</button><button class="danger" data-action="cancel-solve">取消分析</button></div><p class="subtle" id="runStatus" style="margin-top:14px"></p><p class="subtle" style="font-size:10px;margin-top:10px">本轮选择仅对当前任务生效。方向默认值在“方向与模型”页面配置。</p></section>
    <section class="pane-section"><details><summary class="subtle">手动提交 Flag</summary><div class="flag-entry"><input id="flagValue" aria-label="Flag" placeholder="flag{…}" autocomplete="off"><button class="secondary" data-action="submit-flag">提交</button></div></details></section></div>
    <section class="activity-pane"><div class="activity-head"><h3>分析与调用记录</h3><select id="journalKind" aria-label="日志类型"><option value="solve">做题日志</option><option value="agent">Agent 调用日志</option></select></div><div class="log-summary"><span id="journalCount"></span><button class="quiet" data-action="clear-display">清空显示</button></div><div class="terminal" id="journal" tabindex="0" aria-label="题目日志"></div><div class="log-downloads"><button data-action="download-journal" data-kind="solve">↓ 完整做题日志</button><button data-action="download-journal" data-kind="agent">↓ Agent 调用日志</button></div></section></div>`;
  syncSolveControls();
}
function syncSolveControls() {
  if (!state.detail?.challenge || !$('runStatus')) return;
  const {status} = state.detail, active = activeStates.has(status);
  const availability = {'start-solve':!active,'pause-solve':status === 'running','resume-solve':['paused','pausing'].includes(status),'cancel-solve':active && status !== 'cancelling','clear-display':!active,'check-connection':!active};
  for (const [action,available] of Object.entries(availability)) {
    const button = document.querySelector(`[data-action="${action}"]`); if (button) button.disabled = !available || state.busy.has(action);
    if (button && ['pause-solve','resume-solve','cancel-solve'].includes(action)) button.hidden = action === 'cancel-solve' ? !active : !available;
  }
  $('runModel').disabled = active;
  $('runStatus').textContent = `任务状态：${statuses[status] || status}${active && state.detail.model ? ` · ${state.detail.model}` : ''}`;
}
function updateLogs(log) {
  if (!state.detail || !$('journal')) return;
  const terminal = $('journal'), follow = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 70;
  const entries = (log.logs || []).slice(-200), signature = JSON.stringify(entries);
  if (signature !== state.detail.signature) {
    terminal.innerHTML = entries.map(entry => `<article class="log-line" data-type="${escapeHTML(entry.type)}"><div class="log-head"><span>${escapeHTML(entry.timestamp)}</span><strong>${escapeHTML(entry.type)}</strong>${entry.metadata?.round ? `<span>第 ${number(entry.metadata.round)} 轮</span>` : ''}</div><div class="log-content">${escapeHTML(entry.content)}</div></article>`).join('') || '<div class="empty">暂无日志。<br>新一轮分析将追加到完整历史中。</div>';
    state.detail.signature = signature;
    if (follow) terminal.scrollTop = terminal.scrollHeight;
  }
  $('journalCount').textContent = `显示最近 ${entries.length} 条 · 完整记录 ${number(log.log_count)} 条`;
  state.detail.status = log.status || 'idle';
  if (activeStates.has(state.detail.status) && log.model && state.models.some(model => model.name === log.model)) {
    state.detail.model = log.model; $('runModel').value = log.model;
  }
  syncSolveControls();
}
async function refreshLogs() {
  const detail = state.detail, revision = state.logRevision;
  if (!detail?.challenge || detail.loading) return;
  detail.loading = true; const kind = detail.kind;
  try {
    const log = await api(`/api/solve-log/${encode(detail.platform)}/${encode(detail.id)}?kind=${encode(kind)}&limit=200`,{platform:detail.platform});
    if (detail === state.detail && revision === state.logRevision && kind === detail.kind) updateLogs(log.data);
  } finally { detail.loading = false; }
}
function schedulePoll() {
  clearTimeout(pollTimer);
  if (!state.detail) return;
  const detail = state.detail;
  pollTimer = setTimeout(async () => {
    try { await refreshLogs(); } catch { $('serviceStatus').textContent = '连接中断'; }
    if (detail === state.detail) schedulePoll();
  },activeStates.has(detail.status) ? 1800 : 8000);
}
async function download(path,filename,platform=state.platform) {
  const response = await fetch(path,{headers:{'X-Platform-Id':platform}});
  if (!response.ok) throw new Error(`下载失败（HTTP ${response.status}）`);
  const url = URL.createObjectURL(await response.blob()), link = document.createElement('a');
  link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),10000);
}
async function solveAction(action) {
  const detail = state.detail, platform = detail.platform;
  if (action === 'start-solve') {
    const result = await api(`/api/solve/${encode(platform)}/${encode(detail.id)}`,{method:'POST',body:{challenge_id:detail.id,model:$('runModel').value || null},platform});
    if (result.data.started) { detail.status = 'running'; detail.model = $('runModel').value; }
    notify(result.message);
  } else {
    const operation = action.split('-')[0];
    const result = await api(`/api/solve/${encode(platform)}/${encode(detail.id)}/${operation}`,{method:'POST',platform});
    detail.status = result.data.status;
  }
  if (detail === state.detail) { syncSolveControls(); await refreshLogs(); schedulePoll(); }
}
async function action(button) {
  const name = button.dataset.action, detail = state.detail;
  switch (name) {
    case 'refresh': await loadWorkspace(); notify('题库已刷新。'); break;
    case 'open-question': await openQuestion(button.dataset.id); break;
    case 'back-questions': navigate('challenges'); await loadWorkspace(); break;
    case 'new-model': editModel(); break;
    case 'edit-model': editModel(button.dataset.name); break;
    case 'delete-model':
      if (!confirm(`删除模型连接“${button.dataset.name}”？`)) return;
      await api(`/api/models/${encode(button.dataset.name)}`,{method:'DELETE',platform:''}); await loadWorkspace(); notify('模型连接已删除。'); break;
    case 'save-direction': {
      const category = button.dataset.category, card = button.closest('.direction-card');
      await api(`/api/category-models/${encode(category)}`,{method:'PUT',body:{model:card.querySelector('select').value || null},platform:''});
      await loadWorkspace(); notify(`${category} 方向默认模型已保存。`); break;
    }
    case 'refresh-usage': await renderUsage(); break;
    case 'attachment': await download(`/api/challenges/${encode(detail.id)}/download?filename=${encode(button.dataset.filename)}`,button.dataset.filename,detail.platform); break;
    case 'download-journal': await download(`/api/solve-log/${encode(detail.platform)}/${encode(detail.id)}/download?kind=${encode(button.dataset.kind)}`,`${detail.platform}-${detail.id}-${button.dataset.kind}.jsonl`,detail.platform); break;
    case 'clear-display':
      await api(`/api/solve-log/${encode(detail.platform)}/${encode(detail.id)}`,{method:'DELETE',platform:detail.platform});
      if (detail === state.detail) { detail.signature = null; await refreshLogs(); } notify('显示已清空，完整日志仍然保留。'); break;
    case 'check-connection': {
      const output = $('connectionResult'); output.textContent = '正在检查连接…';
      try { const result = await api('/api/models/check',{method:'POST',body:{challenge_id:detail.id,model:$('runModel').value || null},platform:detail.platform}); if (detail === state.detail) output.textContent = result.data.message; }
      catch (error) { if (detail === state.detail) output.textContent = error.message; } break;
    }
    case 'start-solve': case 'pause-solve': case 'resume-solve': case 'cancel-solve': await solveAction(name); break;
    case 'environment-start': case 'environment-stop': case 'environment-status': {
      const operation = name.split('-')[1], path = `/api/containers/${encode(detail.id)}${operation === 'status' ? '' : '/'+operation}`;
      const result = await api(path,{method:operation === 'status' ? 'GET' : 'POST',platform:detail.platform});
      if (detail === state.detail) $('environmentInfo').textContent = `${result.data.status}\n${result.data.connection_info || ''}`; break;
    }
    case 'submit-flag': {
      const flag = $('flagValue').value.trim(); if (!flag) throw new Error('请先填写 Flag。');
      const result = await api(`/api/challenges/${encode(detail.id)}/submit`,{method:'POST',body:{challenge_id:detail.id,flag},platform:detail.platform});
      notify(result.data.message || (result.data.is_correct ? 'Flag 正确。' : 'Flag 未通过。')); break;
    }
  }
}
document.addEventListener('click',event => {
  const close = event.target.closest('[data-close]'); if (close) { $(close.dataset.close).close(); return; }
  const view = event.target.closest('[data-view]'); if (view) { navigate(view.dataset.view); return; }
  const button = event.target.closest('[data-action]'); if (button && !button.disabled) exclusive(button.dataset.action,button,() => action(button));
});
document.addEventListener('input',event => { if (event.target.id === 'questionSearch') { state.query = event.target.value; renderRows(); } });
document.addEventListener('change',async event => {
  if (event.target.id === 'directionFilter') { state.category = event.target.value; renderRows(); }
  if (event.target.id === 'solvedFilter') { state.solved = event.target.value; renderRows(); }
  if (event.target.id === 'runModel' && state.detail) state.detail.model = event.target.value;
  if (event.target.id === 'journalKind' && state.detail) {
    state.detail.kind = event.target.value; state.detail.signature = null;
    await exclusive('journal-switch',event.target,async () => { while (state.detail?.loading) await new Promise(resolve => setTimeout(resolve,30)); await refreshLogs(); });
  }
});
$('platformPicker').addEventListener('change',() => exclusive('platform-switch',$('platformPicker'),async () => {
  stopPolling(); state.detail = null; state.platform = $('platformPicker').value;
  localStorage.setItem('ctf-v2-platform',state.platform); state.query = ''; state.category = ''; state.solved = '';
  $('workspace').innerHTML = '<div class="empty">正在切换平台…</div>'; await loadWorkspace();
}));
$('connectionForm').addEventListener('submit',event => { event.preventDefault(); exclusive('save-model',event.submitter,() => saveModel(event.currentTarget)); });
$('registerPlatform').addEventListener('click',() => { $('platformForm').reset(); $('platformEditorError').textContent = ''; $('platformEditor').showModal(); });
$('platformForm').addEventListener('submit',event => {
  event.preventDefault(); const form = event.currentTarget;
  exclusive('register-platform',event.submitter,async () => {
    try {
      const result = await api('/api/platforms',{method:'POST',body:Object.fromEntries(new FormData(form)),platform:''});
      $('platformEditor').close(); state.platforms = (await api('/api/platforms',{platform:''})).data;
      state.platform = result.data.id; renderPlatformPicker(); navigate('challenges'); await loadWorkspace(); notify('平台已创建，请确认服务端 Token 已配置。');
    } catch (error) { $('platformEditorError').textContent = error.message; }
  });
});
function setAppearance(value) { document.documentElement.dataset.appearance = value; localStorage.setItem('ctf-v2-appearance',value); }
setAppearance(localStorage.getItem('ctf-v2-appearance') || 'light');
$('appearance').addEventListener('click',() => setAppearance(document.documentElement.dataset.appearance === 'dark' ? 'light' : 'dark'));
function renderPlatformPicker() {
  $('platformPicker').innerHTML = state.platforms.map(platform => `<option value="${escapeHTML(platform.id)}">${escapeHTML(platform.name || platform.id)}</option>`).join(''); $('platformPicker').value = state.platform;
}
async function initialize() {
  const [health,platforms] = await Promise.all([api('/api/health',{platform:''}),api('/api/platforms',{platform:''})]);
  $('serviceStatus').textContent = health.status === 'ok' ? '服务在线' : '连接异常'; state.platforms = platforms.data;
  const saved = localStorage.getItem('ctf-v2-platform'); state.platform = state.platforms.some(item => item.id === saved) ? saved : state.platforms.find(item => item.id === 'local')?.id || state.platforms[0]?.id || '';
  renderPlatformPicker(); if (state.platform) await loadWorkspace(); else render();
}
initialize().catch(error => { $('serviceStatus').textContent = '连接失败'; $('workspace').innerHTML = `<div class="empty"><strong>工作空间加载失败</strong>${escapeHTML(error.message)}<br><button class="secondary" onclick="location.reload()">重新加载</button></div>`; });
