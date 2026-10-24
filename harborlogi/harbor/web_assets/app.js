'use strict';
const $ = id => document.getElementById(id);
const labels = {
  PLANNED: '계획 검토', SUBMITTING: '접수 결과 확인 필요', WAITING: '진행 대기', HOLD: '보류',
  AWAITING_FINAL_EVIDENCE: '최종 근거 대기', COMPLETED: '업무 완료',
  PROPOSED: 'Fleet 승인 대기', READY: '실행 조건 대기', RUNNING: '수행 중',
  ACTION_SUCCEEDED: 'Action 성공 · 물리 근거 대기', GOAL_CONFIRMED: '물리 목표 확인',
  DEMO: '합성 상태 · 연결 안 함', NOT_SUBMITTED: '아직 제안하지 않음',
  NO_CURRENT_JOB: '현재 단계의 실행 없음',
  NOT_CONFIGURED: 'Fleet 연결 설정 필요', UNAVAILABLE: '현재 조회할 수 없음',
  FORBIDDEN: 'Fleet 조회 권한 확인 필요', MISMATCH: '원래 요청과 일치하지 않음',
  STALE: '조회 중 업무 변경 · 다시 열기', UNSUPPORTED: '호환 경로 별도 대조',
  VERIFIED: '원래 job의 receipt 대조됨', NOT_VERIFIED: '미확인'
};
let tasks = [], selected = null, filter = 'ALL', generation = 0, mission = null, listAvailable = false;
const text = (id, value) => { $(id).textContent = value ?? '미확인'; };
const label = value => labels[value] || '미확인';
function element(tag, content, className) {
  const node = document.createElement(tag);
  if (content !== undefined) node.textContent = content;
  if (className) node.className = className;
  return node;
}
async function api(key, signal) {
  const response = await fetch('/api/work' + (key == null ? '' : '?key=' + encodeURIComponent(key)),
    {cache: 'no-store', signal});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || '조회하지 못했습니다. 다시 시도하세요.');
  return data;
}
function renderTasks() {
  if (!listAvailable) {
    $('tasks').replaceChildren();
    text('list-count', '업무 수 미확인');
    return;
  }
  const search = $('search').value.trim().toLocaleLowerCase();
  const shown = tasks.filter(task => (filter === 'ALL' || task.state === filter) &&
    (task.request_key + ' ' + task.cargo_id).toLocaleLowerCase().includes(search));
  $('tasks').replaceChildren();
  text('list-count', `${shown.length}개 업무`);
  for (const task of shown) {
    const item = element('li');
    const button = element('button', undefined, 'task');
    button.type = 'button';
    button.setAttribute('aria-current', String(task.request_key === selected));
    button.append(element('strong', task.request_key));
    const meta = element('span', undefined, 'task-meta');
    meta.append(element('span', '화물 ' + task.cargo_id),
      element('span', label(task.state), 'badge' + (task.state === 'HOLD' ? ' hold' : '')));
    button.append(meta);
    button.addEventListener('click', () => openWork(task.request_key, true));
    item.append(button);
    $('tasks').append(item);
  }
  if (!shown.length) $('tasks').append(element('li', tasks.length ? '검색 조건에 맞는 업무가 없습니다.' : '장부에 등록된 업무가 없습니다.', 'muted'));
}
function facts(id, entries) {
  const list = $(id);
  list.replaceChildren();
  for (const [name, value] of entries) list.append(element('dt', name), element('dd', value ?? '미확인'));
}
function nextMessage(detail) {
  if (detail.mode === 'demo') return '이 화면은 합성 시안입니다. 작업 선택·검색·상태 구분을 확인할 수 있으며 ROSY에 명령을 보내지 않습니다.';
  if (detail.fleet.status === 'STALE') return '업무가 바뀌었습니다. 목록을 새로고침한 뒤 현재 단계를 다시 확인하세요.';
  if (['MISMATCH', 'FORBIDDEN', 'UNAVAILABLE', 'NOT_CONFIGURED'].includes(detail.fleet.status))
    return '실행 상태를 확인하지 못했습니다. 서버의 연결 설정·조회 권한과 원래 mission ID를 확인하세요. 재접수하지 마세요.';
  if (detail.business.state === 'COMPLETED') return '업무 장부에는 완료로 기록되어 있습니다. 현재 물리 상태의 최신성을 뜻하지 않습니다.';
  if (detail.business.state === 'HOLD' || detail.business.state === 'SUBMITTING')
    return '원래 요청의 상태와 부족한 근거를 확인하세요. 새 key로 재접수하거나 장부를 초기화하지 마세요.';
  if (detail.fleet.status === 'PROPOSED') return 'mission ID를 복사하고 Fleet 콘솔에서 작업을 검토하세요. 승인은 Fleet의 운영자 세션에서 진행합니다.';
  if (detail.business.state === 'PLANNED') return '계획의 화물·이동 순서·팔 역할을 검토하세요. 현재 웹 단계에서는 제안 쓰기를 열지 않았습니다.';
  return 'ROSY 실행 상태와 Harbor 업무 근거를 각각 확인하세요. Action 성공이나 물리 receipt만으로 업무가 완료되지는 않습니다.';
}
function renderDetail(detail) {
  const work = detail.business, fleet = detail.fleet, receipt = detail.receipt;
  mission = work.mission_id;
  text('cargo', '화물 ' + work.cargo_id);
  text('work-title', work.request_key);
  text('plan-meta', `계획 revision ${work.revision} · 장부 버전 ${work.row_version} · ${work.execution_kind}`);
  text('business-status', label(work.state));
  $('business-status').className = 'badge' + (work.state === 'HOLD' ? ' hold' : '');
  text('step-label', `장부상 ${Math.min(work.step_index + 1, work.step_count)} / ${work.step_count} 단계`);
  text('next-message', nextMessage(detail));
  $('moves').replaceChildren();
  detail.plan.forEach((move, index) => {
    const item = element('li', undefined, index === work.step_index ? 'current' : '');
    const content = element('div');
    content.append(element('strong', '화물 ' + move.cargo_id), element('p', `${move.source} → ${move.destination}`),
      element('p', '주 팔 ' + move.arms[0] + (move.arms.length > 1 ? ' · 대체 ' + move.arms.slice(1).join(', ') : ' · 대체 없음')),
      element('span', index < work.step_index ? '장부상 이전 단계' : index === work.step_index ? '현재 단계' : '이후 단계', 'move-state'));
    item.append(content);
    $('moves').append(item);
  });
  text('fleet-status', label(fleet.status));
  text('fleet-message', fleet.status === 'DEMO' ? fleet.reason : 'Fleet 조회 결과입니다. Harbor의 업무 완료 판정과 구분합니다.');
  facts('fleet-fields', [['mission ID', work.mission_id], ['승인 principal', fleet.approved_by],
    ['authority epoch', fleet.authority_epoch], ['dispatch generation', fleet.dispatch_generation],
    ['원본 관측 시각', null]]);
  $('copy').disabled = !mission;
  $('console').hidden = !detail.console_url;
  if (detail.console_url) $('console').href = detail.console_url;
  else $('console').removeAttribute('href');
  text('handoff-note', detail.mode === 'demo' ? '데모에서는 Fleet 콘솔을 연결하지 않습니다.' :
    detail.console_url ? 'Fleet에서 별도로 로그인한 뒤 복사한 ID로 대조하세요. 상세 화면 지원은 Fleet 설치를 확인해야 합니다.' : 'Fleet 콘솔 주소가 설정되지 않았습니다. 운영 담당에게 원래 mission ID를 전달하세요.');
  text('copy-status', '');
  text('receipt-status', label(receipt.status));
  facts('receipt-fields', [['receipt ID', receipt.receipt_id], ['Action ID', receipt.action_id], ['attempt ID', receipt.attempt_id]]);
  text('business-evidence', work.state === 'COMPLETED' ? '장부 기록: 업무 완료' : '장부 기록: ' + label(work.state) + ' · 최종 업무 완료 전');
  text('read-time', '화면 조회 시각: ' + new Date(detail.read_at).toLocaleString('ko-KR') + ' · 원본 관측 시각 아님');
}
async function openWork(key, focus = false) {
  const ticket = ++generation;
  selected = key;
  renderTasks();
  $('welcome').hidden = true;
  $('detail').hidden = true;
  $('detail-error').hidden = false;
  text('detail-error', '업무와 ROSY 상태를 조회하고 있습니다…');
  $('detail').setAttribute('aria-busy', 'true');
  $('refresh').disabled = true;
  // The HTTP timeout belongs to Fleet; this also bounds a stalled browser connection.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const detail = await api(key, controller.signal);
    if (ticket !== generation) return;
    renderDetail(detail);
    $('detail-error').hidden = true;
    $('detail').hidden = false;
    history.replaceState(null, '', '/?key=' + encodeURIComponent(key));
    if (focus) $('work-title').focus();
  } catch (error) {
    if (ticket !== generation) return;
    text('detail-error', error.name === 'AbortError' ? '조회가 지연되고 있습니다. 목록에서 업무를 다시 여세요.' : error.message);
  } finally {
    clearTimeout(timeout);
    if (ticket === generation) {
      $('detail').setAttribute('aria-busy', 'false');
      $('refresh').disabled = false;
    }
  }
}
async function load() {
  $('reload').disabled = true;
  $('list-error').hidden = true;
  try {
    const data = await api();
    tasks = data.tasks;
    listAvailable = true;
    text('mode', data.mode === 'demo' ? 'DEMO · 합성 데이터' : '실제 장부 조회');
    $('mode-note').className = 'mode-note' + (data.mode === 'demo' ? ' demo' : '');
    text('mode-note', data.mode === 'demo' ? '합성 시안 · 로봇과 연결하지 않았습니다. 현장 관측이나 실행 증거가 아닙니다.' : '조회 전용 · 기존 장부를 변경하지 않습니다. ROSY 연결 실패 시 미확인으로 표시합니다.');
    renderTasks();
    const key = selected || new URLSearchParams(location.search).get('key');
    if (key) await openWork(key);
  } catch (error) {
    ++generation;
    tasks = [];
    listAvailable = false;
    renderTasks();
    $('detail').hidden = true;
    $('welcome').hidden = false;
    text('mode', '장부 조회 실패');
    $('list-error').hidden = false;
    text('list-error', '업무를 불러오지 못했습니다. 서버 설정을 확인한 뒤 목록을 새로고침하세요.');
  } finally { $('reload').disabled = false; }
}
$('search').addEventListener('input', renderTasks);
document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => {
  filter = button.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach(node => node.setAttribute('aria-pressed', String(node === button)));
  renderTasks();
}));
$('reload').addEventListener('click', load);
$('refresh').addEventListener('click', () => openWork(selected));
$('copy').addEventListener('click', async () => {
  if (!mission) return;
  try { await navigator.clipboard.writeText(mission); text('copy-status', 'mission ID를 복사했습니다.'); }
  catch { text('copy-status', '자동 복사가 허용되지 않았습니다. 위의 mission ID를 선택해 복사하세요.'); }
});
load();
