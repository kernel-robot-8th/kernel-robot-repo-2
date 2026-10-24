'use strict';
const $ = id => document.getElementById(id);
const labels = Object.assign(Object.create(null), {
  PLANNED: '계획 검토', SUBMITTING: '접수 결과 확인 필요', WAITING: '장부상 대기',
  HOLD: '보류', AWAITING_FINAL_EVIDENCE: '최종 근거 대기', COMPLETED: '업무 완료',
  PROPOSED: 'Fleet 승인 대기', READY: '실행 조건 대기', RUNNING: '수행 중',
  ACTION_SUCCEEDED: 'Action 성공 · 물리 근거 대기', GOAL_CONFIRMED: '물리 목표 확인',
  DEMO: '합성 상태 · 연결 안 함', NOT_SUBMITTED: '아직 제안하지 않음',
  NO_CURRENT_JOB: '현재 단계의 실행 없음', NOT_CONFIGURED: 'Fleet 연결 설정 필요',
  UNAVAILABLE: '현재 조회할 수 없음', FORBIDDEN: 'Fleet 조회 권한 확인 필요',
  MISMATCH: '원래 요청과 일치하지 않음', STALE: '조회 중 업무 변경 · 다시 열기',
  UNSUPPORTED: '호환 경로 별도 대조', VERIFIED: '원래 job의 물리 근거 대조됨', NOT_VERIFIED: '미확인'
});
const attentionRules = Object.assign(Object.create(null), {
  SUBMITTING: {label: '접수 결과 대조', reason: '원래 요청이 접수됐는지 확인해야 합니다.', action: '요청 확인', tab: 'execution'},
  HOLD: {label: '보류 업무', reason: '원래 요청의 상태와 부족한 근거를 대조해야 합니다.', action: '업무 확인', tab: 'overview'},
  AWAITING_FINAL_EVIDENCE: {label: '최종 근거 대기', reason: '이동 이후 업무 완료에 필요한 근거를 확인해야 합니다.', action: '근거 확인', tab: 'execution'}
});
const viewStates = Object.assign(Object.create(null), {planned: 'PLANNED', waiting: 'WAITING', hold: 'HOLD', evidence: 'AWAITING_FINAL_EVIDENCE', completed: 'COMPLETED'});
let tasks = [], listAvailable = false, mode = null, readAt = null, generation = 0, mission = null;
let lastContext = {view: 'all', q: ''}, menuOpen = false;
const listScroll = new Map();
const text = (id, value) => { $(id).textContent = value ?? '미확인'; };
const label = value => labels[value] || '미확인';
function node(tag, content, className) {
  const item = document.createElement(tag);
  if (content !== undefined) item.textContent = content;
  if (className) item.className = className;
  return item;
}
function link(title, href, className) {
  const item = node('a', title, className);
  item.href = href; item.dataset.route = '';
  return item;
}
function badge(state) {
  return node('span', label(state), 'badge ' + (state === 'COMPLETED' ? 'success' : attentionRules[state] ? 'warning' : labels[state] ? '' : 'unknown'));
}
function context() {
  const params = new URLSearchParams(location.search);
  const view = params.get('view') || 'all';
  return {view: view === 'needs-action' || view === 'all' || view in viewStates ? view : 'all', q: params.get('q') || ''};
}
function query(ctx) {
  const params = new URLSearchParams();
  if (ctx.view !== 'all') params.set('view', ctx.view);
  if (ctx.q) params.set('q', ctx.q);
  return params.size ? '?' + params.toString() : '';
}
function workUrl(key, tab = 'overview', ctx = lastContext) {
  const encoded = key ? encodeURIComponent(key) : '';
  // Existing ledger keys may contain URL path separators or dot segments.
  if (key === '.' || key === '..' || /%2F|%5C/.test(encoded)) {
    const params = new URLSearchParams(query(ctx));
    params.set('key', key);
    if (tab !== 'overview') params.set('tab', tab);
    return '/work?' + params.toString();
  }
  return '/work' + (key ? '/' + encoded : '') + (key && tab !== 'overview' ? '/' + tab : '') + query(ctx);
}
function route() {
  if (location.pathname === '/work') {
    const params = new URLSearchParams(location.search), tab = params.get('tab');
    return {page: 'work', key: params.get('key') || null, tab: ['plan', 'execution'].includes(tab) ? tab : 'overview'};
  }
  const match = location.pathname.match(/^\/work\/([^/]+)(?:\/(plan|execution))?$/);
  if (match) {
    try { return {page: 'work', key: decodeURIComponent(match[1]), tab: match[2] || 'overview'}; }
    catch { return {page: 'work', key: null, tab: 'overview'}; }
  }
  return {page: location.pathname === '/help' ? 'help' : location.pathname === '/planning' ? 'planning' : 'overview'};
}
function filtered(ctx = context()) {
  const search = ctx.q.trim().toLocaleLowerCase();
  return tasks.filter(work => (ctx.view === 'all' || ctx.view === 'needs-action' && attentionRules[work.state] ||
      viewStates[ctx.view] === work.state) &&
    (work.request_key + ' ' + work.cargo_id).toLocaleLowerCase().includes(search));
}
function formatTime(value) {
  return value ? new Date(value).toLocaleString('ko-KR') : '미확인';
}
async function requestJSON(path) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(path, {cache: 'no-store', signal: controller.signal});
    const data = await response.json();
    if (!response.ok) {
      const error = new Error('Request unavailable');
      error.apiMessage = data.message;
      throw error;
    }
    return data;
  } finally { clearTimeout(timeout); }
}
function api(key = null) {
  return requestJSON('/api/work' + (key == null ? '' : '?key=' + encodeURIComponent(key)));
}
function showMode() {
  $('nav-planning').hidden = !listAvailable || mode !== 'demo';
  text('mode', listAvailable ? mode === 'demo' ? 'DEMO · 합성' : '실제 장부' : '조회 미확인');
  $('mode').className = 'environment' + (mode === 'demo' ? ' demo' : '');
  $('mode-note').className = 'mode-note' + (mode === 'demo' ? ' demo' : '');
  text('mode-note', mode === 'demo' ? '합성 데이터로 보는 Harbor · 로봇에 연결하지 않았으며 현장 관측이나 실행 증거가 아닙니다.' :
    '조회 전용 · 업무 장부와 ROSY 실행 근거를 구분합니다. 조회 시각은 원본 관측 시각이 아닙니다.');
}
async function loadList() {
  $('reload').disabled = true;
  try {
    const data = await api();
    if (!Array.isArray(data.tasks) || !['demo', 'live'].includes(data.mode)) throw new Error('Invalid response');
    tasks = data.tasks;
    listAvailable = true;
    mode = data.mode;
    readAt = new Date().toISOString();
  } catch {
    tasks = []; listAvailable = false; readAt = null;
  } finally {
    showMode();
    $('reload').disabled = false;
  }
}
function renderOverview() {
  const attention = tasks.filter(work => attentionRules[work.state]);
  text('attention-count', listAvailable ? attention.length : '미확인');
  text('nav-attention', listAvailable ? attention.length || '' : '—');
  text('overview-read', readAt ? '장부 조회 ' + formatTime(readAt) : '장부 조회 시각 미확인');
  $('overview-error').hidden = listAvailable;
  text('overview-error', '업무 장부를 읽지 못했습니다. 연결 설정을 확인한 뒤 목록을 새로고침하세요. 업무 수는 미확인입니다.');
  $('attention').replaceChildren();
  if (!listAvailable) $('attention').append(node('li', '현재 조치 필요 업무를 확인할 수 없습니다.', 'empty-message'));
  else if (!attention.length) $('attention').append(node('li', '장부 기준으로 조치가 필요한 업무가 없습니다. 실제 장비 정상이나 근거 최신성을 뜻하지 않습니다.', 'empty-message'));
  for (const work of attention) {
    const rule = attentionRules[work.state], ctx = {view: 'needs-action', q: ''};
    const row = node('li', undefined, 'attention-row');
    const ref = node('div', undefined, 'attention-ref');
    ref.append(link(work.request_key, workUrl(work.request_key, 'overview', ctx)), node('p', '화물 ' + work.cargo_id));
    const reason = node('div', undefined, 'attention-reason');
    reason.append(node('span', rule.label, 'badge warning'), node('p', rule.reason));
    row.append(ref, reason, node('span', '담당 미지정', 'attention-owner'), link(rule.action, workUrl(work.request_key, rule.tab, ctx), 'attention-action'));
    $('attention').append(row);
  }
  $('waiting').replaceChildren();
  const waiting = tasks.filter(work => work.state === 'WAITING');
  if (!listAvailable || !waiting.length) $('waiting').append(node('li', listAvailable ? '대기 상태의 업무가 없습니다.' : '대기 업무도 미확인입니다.', 'metadata'));
  for (const work of waiting.slice(0, 4)) {
    const row = node('li'), item = link('', workUrl(work.request_key, 'overview', {view: 'waiting', q: ''}));
    item.append(node('span', work.request_key + ' · 화물 ' + work.cargo_id), badge(work.state));
    row.append(item); $('waiting').append(row);
  }
}
function renderTasks() {
  const ctx = context(), current = route();
  $('search').value = ctx.q; $('view').value = ctx.view;
  $('tasks').replaceChildren();
  text('list-count', listAvailable ? filtered(ctx).length + '개 업무' : '업무 수 미확인');
  $('list-error').hidden = listAvailable;
  text('list-error', '장부 조회 실패 · 서버 설정을 확인한 뒤 목록을 새로고침하세요.');
  if (!listAvailable) return;
  const shown = filtered(ctx);
  if (!shown.length) $('tasks').append(node('li', tasks.length ? '검색 조건에 맞는 업무가 없습니다.' : '장부에 등록된 업무가 없습니다.', 'empty-message'));
  for (const work of shown) {
    const item = node('li'), anchor = link('', workUrl(work.request_key, 'overview', ctx), 'task');
    if (current.key === work.request_key) anchor.setAttribute('aria-current', 'location');
    anchor.append(node('strong', work.request_key));
    const meta = node('span', undefined, 'task-meta');
    meta.append(node('span', '화물 ' + work.cargo_id), badge(work.state));
    anchor.append(meta); item.append(anchor); $('tasks').append(item);
  }
  $('outside-filter').hidden = !current.key || shown.some(work => work.request_key === current.key);
}
function facts(id, values) {
  $(id).replaceChildren();
  for (const [name, value] of values) $(id).append(node('dt', name), node('dd', value ?? '미확인'));
}
function nextMessage(detail) {
  if (['MISMATCH', 'FORBIDDEN', 'UNAVAILABLE', 'NOT_CONFIGURED', 'STALE'].includes(detail.fleet.status))
    return '실행 상태를 확인하지 못했습니다. 원래 mission ID와 조회 권한·연결을 대조하세요. 새 요청으로 재접수하지 마세요.';
  if (attentionRules[detail.business.state]) return attentionRules[detail.business.state].reason +
    ' 구체 원인과 담당자는 아직 확인되지 않았습니다.';
  if (detail.business.state === 'COMPLETED') return '장부에는 업무 완료로 기록되어 있습니다. 현재 물리 상태와 원본 관측의 최신성을 보증하는 표시는 아닙니다.';
  if (detail.business.state === 'PLANNED') return '계획의 화물·이동 순서·팔 역할을 검토하세요. 현재 화면에서는 계획을 조회할 수 있습니다.';
  if (detail.fleet.status === 'PROPOSED') return 'mission ID를 복사해 Fleet 콘솔에서 원래 작업을 확인하세요. 승인은 운영자의 별도 Fleet 세션에서 처리합니다.';
  return '업무 장부는 대기 상태입니다. ROSY 실행과 물리 receipt, 최종 업무 근거를 각각 확인하세요.';
}
function setTaskLinks(current, ctx) {
  $('back-work').href = workUrl(null, 'overview', ctx);
  $('open-plan').href = workUrl(current.key, 'plan', ctx);
  $('open-execution').href = workUrl(current.key, 'execution', ctx);
  for (const tab of ['overview', 'plan', 'execution']) {
    const active = current.tab === tab;
    const anchor = $('tab-' + tab);
    anchor.href = workUrl(current.key, tab, ctx);
    anchor.setAttribute('aria-selected', String(active));
    anchor.tabIndex = active ? 0 : -1;
    $('pane-' + tab).hidden = !active;
  }
}
function renderDetail(detail, current) {
  const work = detail.business, fleet = detail.fleet, receipt = detail.receipt;
  mission = work.mission_id;
  text('work-title', '화물 ' + work.cargo_id + ' 반출');
  text('business-key', work.request_key);
  $('business-status').replaceWith(Object.assign(badge(work.state), {id: 'business-status'}));
  text('step-label', work.step_index >= work.step_count ? '계획된 이동 단계의 대조를 마쳤습니다.' :
    '장부상 ' + (work.step_index + 1) + ' / ' + work.step_count + ' 단계');
  text('next-message', nextMessage(detail));
  $('next-action').className = 'next-action' + (attentionRules[work.state] ? ' warning' : '');
  const move = detail.plan[work.step_index];
  $('current-move').replaceChildren();
  if (move) $('current-move').append(node('strong', '화물 ' + move.cargo_id), node('p', move.source + ' → ' + move.destination),
    node('p', '주 팔 ' + move.arms[0] + (move.arms.length > 1 ? ' · 대체 ' + move.arms.slice(1).join(', ') : ' · 대체 없음'), 'metadata'));
  else $('current-move').append(node('p', '남은 이동 단계가 없습니다. 최종 업무 근거와 장부 상태를 확인하세요.'));
  facts('business-fields', [['장부 상태', label(work.state)], ['계획 revision', work.revision], ['장부 버전', work.row_version]]);
  text('plan-meta', '계획 revision ' + work.revision);
  $('moves').replaceChildren();
  detail.plan.forEach((plan, index) => {
    const row = node('li', undefined, index === work.step_index ? 'current' : ''), content = node('div');
    content.append(node('strong', '화물 ' + plan.cargo_id), node('p', plan.source + ' → ' + plan.destination),
      node('p', '주 팔 ' + plan.arms[0] + (plan.arms.length > 1 ? ' · 대체 ' + plan.arms.slice(1).join(', ') : ' · 대체 없음')),
      node('span', index < work.step_index ? '장부상 이전 단계' : index === work.step_index ? '현재 단계' : '이후 단계', 'move-state'));
    row.append(content); $('moves').append(row);
  });
  text('fleet-status', label(fleet.status));
  text('fleet-message', detail.mode === 'demo' ? '합성 데이터입니다. 이 업무의 실제 로봇이나 관측 producer에 연결하지 않았습니다.' :
    '원래 Fleet job의 조회 결과입니다. Harbor 업무 완료 판정과 별도로 읽으세요.');
  facts('mission-fields', [['mission ID', work.mission_id], ['승인 principal', fleet.approved_by], ['원본 관측 시각', null]]);
  facts('fleet-fields', [['authority epoch', fleet.authority_epoch], ['dispatch generation', fleet.dispatch_generation], ['proposal digest', fleet.proposal_digest]]);
  $('copy').disabled = !mission;
  $('console').hidden = !detail.console_url;
  if (detail.console_url) $('console').href = detail.console_url; else $('console').removeAttribute('href');
  text('handoff-note', detail.mode === 'demo' ? '데모에서는 Fleet 콘솔을 연결하지 않습니다.' : detail.console_url ?
    '별도 Fleet 세션이 새 탭에서 열립니다. 복사한 ID로 대조한 뒤 돌아오면 현재 상태를 다시 조회하세요.' :
    'Fleet 콘솔 주소가 설정되지 않았습니다. 원래 mission ID를 운영 담당자에게 전달하세요.');
  text('copy-status', '');
  text('receipt-status', label(receipt.status));
  facts('receipt-fields', [['receipt ID', receipt.receipt_id], ['Action ID', receipt.action_id], ['attempt ID', receipt.attempt_id]]);
  text('business-evidence', label(work.state));
  text('read-time', '이 업무 조회: ' + formatTime(detail.read_at) + ' · 원본 관측 시각 아님');
  setTaskLinks(current, context());
}
async function renderRoute(options = {}) {
  const ticket = ++generation, current = route();
  closeMenu(false);
  const names = {overview: '운영 현황', work: '업무', planning: '재고·반출 계획', help: '사용 안내'};
  text('location', names[current.page]); document.title = 'Harbor · ' + names[current.page];
  for (const page of ['overview', 'work', 'planning', 'help']) {
    $(page + '-page').hidden = current.page !== page;
    $('nav-' + page).removeAttribute('aria-current');
  }
  $('nav-' + current.page).setAttribute('aria-current', 'page');
  renderOverview();
  if (current.page === 'planning') {
    $('planning-form').hidden = mode !== 'demo';
    text('planning-environment', mode === 'demo' ? '합성 계획 미리보기' : '재고 관측 연결 필요');
    const params = new URLSearchParams(location.search);
    for (const [id, key, fallback] of [['planning-cargo', 'cargo', 'C1'], ['planning-scenario', 'scenario', 'normal']]) {
      const value = params.has(key) ? params.get(key) : fallback, select = $(id);
      if (![...select.options].some(option => option.value === value)) select.append(Object.assign(node('option', value + ' · 미확인'), {value}));
      select.value = value;
    }
    calculatePlanning(Boolean(options.focus));
    return;
  }
  if (current.page !== 'work') {
    if (options.focus) $(current.page === 'overview' ? 'overview-title' : 'help-title').focus();
    return;
  }
  lastContext = context();
  $('nav-work').href = workUrl(null);
  $('work-page').classList.toggle('has-detail', Boolean(current.key));
  $('back-work').hidden = !current.key;
  $('detail').hidden = true;
  $('detail-error').hidden = true;
  $('work-welcome').hidden = Boolean(current.key);
  renderTasks();
  $('tasks').scrollTop = listScroll.get(query(context())) || 0;
  if (!current.key) {
    if (options.focus) $('work-list-title').focus();
    return;
  }
  $('detail-error').hidden = false;
  $('detail-error').className = 'notice';
  $('detail-error').setAttribute('role', 'status');
  text('detail-error', '업무와 ROSY 상태를 조회하고 있습니다…');
  $('detail').setAttribute('aria-busy', 'true'); $('refresh').disabled = true;
  try {
    const detail = await api(current.key);
    if (ticket !== generation) return;
    renderDetail(detail, current);
    $('detail-error').hidden = true; $('detail').hidden = false;
    if (options.tabFocus) $('tab-' + current.tab).focus();
    else if (options.focus) $('work-title').focus();
  } catch {
    if (ticket !== generation) return;
    $('detail-error').className = 'error'; $('detail-error').setAttribute('role', 'alert');
    text('detail-error', '이 업무를 조회하지 못했습니다. 목록으로 돌아가 다시 열거나 연결 설정을 확인하세요.');
  } finally {
    if (ticket === generation) { $('detail').setAttribute('aria-busy', 'false'); $('refresh').disabled = false; }
  }
}
function renderBays(id, bays, target, blockers = false) {
  $(id).replaceChildren();
  for (const bay of bays) {
    const section = node('section', undefined, 'bay');
    const names = {yard: '장치 칸', temp: '임시 칸', stand: '반출대'};
    section.append(node('h3', names[bay.bay_id] || bay.bay_id));
    const stack = node('ol', undefined, 'bay-stack'), targetIndex = bay.cargo.indexOf(target);
    if (!bay.cargo.length) stack.append(node('li', '빈 칸', 'metadata'));
    [...bay.cargo].reverse().forEach((cargo, offset) => {
      const level = bay.cargo.length - offset, isTarget = cargo === target;
      const isBlocker = blockers && targetIndex >= 0 && level - 1 > targetIndex;
      const item = node('li', undefined, isTarget ? 'target' : isBlocker ? 'blocker' : '');
      item.append(node('strong', cargo), node('span', level + '단 · ' + (isTarget ? '반출 대상' : isBlocker ? '재취급 대상' : '화물')));
      stack.append(item);
    });
    section.append(stack, node('p', '허용 ' + bay.capacity + '단 · 합성 조건', 'metadata'));
    $(id).append(section);
  }
}
async function calculatePlanning(focus = false) {
  const ticket = ++generation, cargo = $('planning-cargo').value, scenario = $('planning-scenario').value;
  history.replaceState(history.state, '', '/planning?' + new URLSearchParams({cargo, scenario}));
  $('planning-result').hidden = true; $('planning-error').hidden = true;
  $('planning-calculate').disabled = true;
  text('planning-message', '합성 관측을 기준으로 반출 계획을 계산하고 있습니다…');
  try {
    const data = await requestJSON('/api/planning?' + new URLSearchParams({cargo, scenario}));
    if (ticket !== generation) return;
    if (data.mode !== 'demo') throw new Error('Unexpected preview source');
    $('planning-form').hidden = false;
    text('planning-environment', '합성 계획 미리보기');
    const options = data.candidates.map(id => Object.assign(node('option', id), {value: id}));
    if (!data.candidates.includes(cargo)) options.push(Object.assign(node('option', cargo + ' · 관측 미확인'), {value: cargo}));
    $('planning-cargo').replaceChildren(...options); $('planning-cargo').value = cargo;
    renderBays('observed-bays', data.observed_bays, cargo, true);
    text('inventory-source', '합성 source ' + data.source_id + ' · revision ' + data.revision + ' · 관측 ' + formatTime(data.observed_at));
    const ready = data.proposal.status === 'READY';
    text('planning-status', ready ? '계획안 생성' : '계획 보류');
    $('planning-status').className = 'badge' + (ready ? '' : ' warning');
    const reasons = Object.assign(Object.create(null), {
      PROPOSAL_REQUIRES_ADMISSION_AND_EVIDENCE: '재취급·반출·복귀 순서를 검토하세요. 계획안은 실행 허가가 아닙니다.',
      NO_LEGAL_TEMPORARY_BAY: '재취급 화물을 둘 유효한 임시 공간이 부족합니다. 부분 이동을 제안하지 않습니다.',
      OBSERVATION_NOT_CURRENT: '합성 관측이 유효기간을 벗어났습니다. 새 근거와 대조하기 전 계획을 보류합니다.',
      OUTBOUND_NOT_OBSERVED: '반출 대상 화물이 현재 합성 관측에 없습니다.'
    });
    text('planning-reason', reasons[data.proposal.reason] || '계획 조건과 필요한 근거를 다시 확인하세요.');
    $('planning-moves').replaceChildren();
    const phases = {REHANDLE: '재취급', OUTBOUND: '반출', RESTORE: '복귀'};
    for (const move of data.proposal.moves) {
      const row = node('li'), content = node('div');
      content.append(node('strong', phases[move.phase] + ' · ' + move.cargo_id), node('p', move.source + ' → ' + move.destination),
        node('p', '주 팔 ' + move.primary_arm + (move.alternate_arms.length ? ' · 대체 ' + move.alternate_arms.join(', ') : ' · 대체 없음')));
      row.append(content); $('planning-moves').append(row);
    }
    $('projected-bays').replaceChildren();
    if (ready) renderBays('projected-bays', data.projected_bays, cargo);
    text('projected-note', ready ? '계획을 적용했을 때의 예상 배치입니다. 관측 배치를 수정하지 않았습니다.' : '유효한 계획이 없어 예상 완료 배치를 만들지 않았습니다.');
    text('reach-source', '팔 도달 정보는 합성 설치 표입니다. 실제 compile 사전 검증·실물 도달성의 근거가 아닙니다.');
    text('planning-message', (ready ? '계획안 생성' : '계획 보류') + ' · 합성 판단 시각 ' + formatTime(data.reference_time) + ' · 유효기간 ' + data.max_observation_age_seconds + '초는 이 합성 사례의 조건입니다.');
    $('planning-result').hidden = false;
  } catch (error) {
    if (ticket !== generation) return;
    $('planning-error').hidden = false;
    text('planning-error', error.apiMessage || '반출 계획을 읽지 못했습니다. 연결과 입력을 확인한 뒤 다시 계산하세요.');
    text('planning-message', '재고와 계획은 미확인입니다. 빈 재고나 성공 계획으로 대체하지 않습니다.');
  } finally {
    if (ticket === generation) {
      $('planning-calculate').disabled = false;
      if (focus) $('planning-title').focus();
    }
  }
}
function saveLocation() {
  if (route().page === 'work') listScroll.set(query(context()), $('tasks').scrollTop);
  history.replaceState({scrollY: window.scrollY}, '', location.href);
}
async function navigate(href, options = {}) {
  saveLocation();
  history.pushState({scrollY: 0}, '', href);
  await renderRoute({focus: !options.tabFocus, ...options});
  if (!options.tabFocus) window.scrollTo(0, 0);
}
function updateFilter() {
  const ctx = {view: $('view').value, q: $('search').value};
  lastContext = ctx;
  const current = route();
  history.replaceState(history.state, '', workUrl(current.key, current.tab, ctx));
  $('nav-work').href = workUrl(null);
  renderTasks();
  if (route().key) setTaskLinks(route(), ctx);
}
function configureLayout() {
  const styles = getComputedStyle(document.documentElement);
  const desktop = parseFloat(styles.getPropertyValue('--primitive-breakpoint-desktop'));
  const tablet = parseFloat(styles.getPropertyValue('--primitive-breakpoint-tablet'));
  document.body.dataset.layout = innerWidth >= desktop ? 'desktop' : innerWidth >= tablet ? 'tablet' : 'mobile';
  if (document.body.dataset.layout === 'desktop') closeMenu(false);
  $('navigation').inert = document.body.dataset.layout !== 'desktop' && !menuOpen;
}
function closeMenu(restoreFocus = true) {
  if (!menuOpen) return;
  menuOpen = false;
  document.body.classList.remove('menu-open');
  $('backdrop').hidden = true; $('menu-toggle').setAttribute('aria-expanded', 'false');
  $('page').inert = false; document.querySelector('.topbar').inert = false;
  $('navigation').removeAttribute('role'); $('navigation').removeAttribute('aria-modal');
  $('navigation').inert = document.body.dataset.layout !== 'desktop';
  if (restoreFocus) $('menu-toggle').focus();
}
function openMenu() {
  menuOpen = true; $('navigation').inert = false;
  document.body.classList.add('menu-open');
  $('backdrop').hidden = false; $('menu-toggle').setAttribute('aria-expanded', 'true');
  $('navigation').setAttribute('role', 'dialog'); $('navigation').setAttribute('aria-modal', 'true');
  $('page').inert = true; document.querySelector('.topbar').inert = true;
  $('nav-overview').focus();
}
document.addEventListener('click', event => {
  const anchor = event.target.closest('a[data-route]');
  if (!anchor || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigate(anchor.href, {tabFocus: Boolean(anchor.dataset.tab)});
});
document.querySelector('.tabs').addEventListener('keydown', event => {
  const tabs = ['overview', 'plan', 'execution'], current = route();
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const index = tabs.indexOf(current.tab);
  const target = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3;
  navigate(workUrl(current.key, tabs[target], context()), {tabFocus: true});
});
document.addEventListener('keydown', event => {
  if (!menuOpen) return;
  if (event.key === 'Escape') { event.preventDefault(); closeMenu(); }
  if (event.key === 'Tab') {
    const focusable = [...$('navigation').querySelectorAll('a[href], button')].filter(item => item.offsetParent !== null);
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});
$('menu-toggle').addEventListener('click', openMenu);
$('nav-close').addEventListener('click', () => closeMenu());
$('backdrop').addEventListener('click', () => closeMenu());
$('search').addEventListener('input', updateFilter);
$('view').addEventListener('change', updateFilter);
$('reload').addEventListener('click', async () => { await loadList(); await renderRoute(); });
$('refresh').addEventListener('click', () => renderRoute());
$('planning-form').addEventListener('submit', event => { event.preventDefault(); calculatePlanning(); });
$('planning-form').addEventListener('change', () => {
  ++generation;
  $('planning-result').hidden = true; $('planning-error').hidden = true;
  $('planning-calculate').disabled = false;
  text('planning-message', '입력이 바뀌었습니다. 선택한 조건으로 미리보기를 다시 계산하세요.');
});
$('copy').addEventListener('click', async () => {
  if (!mission) return;
  try { await navigator.clipboard.writeText(mission); text('copy-status', 'mission ID를 복사했습니다.'); }
  catch { text('copy-status', '자동 복사가 허용되지 않았습니다. 위의 mission ID를 선택해 복사하세요.'); }
});
window.addEventListener('popstate', async event => {
  await renderRoute({focus: true});
  window.scrollTo(0, event.state?.scrollY || 0);
});
window.addEventListener('resize', configureLayout);
$('reload').setAttribute('aria-label', '목록 새로고침');
configureLayout();
if (location.pathname === '/') {
  const oldKey = new URLSearchParams(location.search).get('key');
  history.replaceState(null, '', oldKey ? workUrl(oldKey) : '/overview');
}
loadList().then(() => renderRoute());
