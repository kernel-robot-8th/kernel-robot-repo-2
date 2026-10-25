import {resizeCells, addArea, sceneAreas} from './map-editor-model.mjs';

const root = document.querySelector('[data-map-editor]');
if (root) mountMapEditor(root);

export function mountMapEditor(root) {
  const data=JSON.parse(document.getElementById('map-editor-data').textContent);
  const initial=structuredClone(data.map);
  let map=structuredClone(initial), selected=map.areas[0].id, fingerprint=null, renderer=null, busy=false, revision=0, pending=false;
  const find=name=>root.querySelector(`[data-map-${name}]`);
  const properties=find('properties'), publish=find('publish'), canvas=find('canvas');
  const roles={storage:'일반 보관',temporary:'임시 적치',outbound:'반출대'};
  const error=message=>{const li=document.createElement('li');li.textContent=message;find('errors').append(li);};
  function dirty() {
    revision++; fingerprint=null; find('save').disabled=true;
    find('status').textContent='수정한 초안 · 다시 검증해야 합니다.';
    find('summary').textContent='초안이 바뀌었습니다. 현재 변경 내용으로 검증하세요.';
    find('errors').replaceChildren();
  }
  async function preview() {
    const expected=revision;
    try {
      const {createRenderer}=await import('./yard-renderer.js');
      if (expected!==revision) return;
      renderer?.dispose();
      renderer=createRenderer(canvas,sceneAreas(map),{showVolumes:true,onError:()=>{
        renderer?.dispose();renderer=null;find('render-error').hidden=false;
      }});
      renderer.setBoxes([]); renderer.fit(); canvas.dataset.ready='true';
      find('render-error').hidden=true;
    } catch (_) {find('render-error').hidden=false;canvas.dataset.ready='false';}
  }
  function showCells(area) {
    find('cell-hint').textContent=area.id==='yard'?'주 보관 칸 ID는 직접 편집할 수 있습니다. ID 변경은 기존 도달 참조를 해제합니다.':'보조 구역 주소는 구역 ID·행·열·층으로 생성됩니다.';
    const cells=area.id==='yard'?area.cells:Array.from({length:area.rows},(_,r)=>Array.from({length:area.columns},(_,c)=>({bay_id:`${area.id}-R${r+1}C${c+1}`,row:r+1,column:c+1}))).flat();
    find('cells').replaceChildren(...cells.map(cell=>{
      const label=document.createElement('label');label.textContent=`${cell.row}행 ${cell.column}열`;
      if(area.id==='yard') {
        const input=document.createElement('input');input.value=cell.bay_id;input.maxLength=80;
        input.addEventListener('change',()=>{cell.bay_id=input.value.trim();dirty();});label.append(input);
      } else {const text=document.createElement('span');text.textContent=cell.bay_id;label.append(text);}
      return label;
    }));
  }
  function selection() {
    pending=false;
    const area=map.areas.find(a=>a.id===selected);
    for(const key of ['id','label','role','rows','columns','levels','x','y']) properties.elements[key].value=area[key];
    properties.elements.id.disabled=area.id==='yard';properties.elements.role.disabled=area.id==='yard';
    find('delete').disabled=area.id==='yard';showCells(area);
    find('areas').querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.area===selected)));
  }
  function render() {
    find('areas').replaceChildren(...map.areas.map(area=>{
      const button=document.createElement('button');button.type='button';button.className='map-area-button';button.dataset.area=area.id;
      const title=document.createElement('strong');title.textContent=area.label;
      const meta=document.createElement('span');meta.textContent=`${area.id} · ${roles[area.role]} · ${area.columns}×${area.rows}×${area.levels}`;
      button.append(title,meta);button.addEventListener('click',()=>{selected=area.id;selection();});return button;
    }));
    const capacity=map.areas.reduce((n,a)=>n+a.rows*a.columns*a.levels,0);
    find('capacity').textContent=`${map.areas.length}개 구역 · ${capacity}개 위치`;
    selection();preview();
  }
  properties.addEventListener('submit',event=>{
    event.preventDefault(); if(busy||!properties.reportValidity()) return;
    const old=map.areas.find(a=>a.id===selected), value={...old};
    for(const key of ['id','label','role']) value[key]=properties.elements[key].value.trim();
    for(const key of ['rows','columns','levels','x','y']) value[key]=Number(properties.elements[key].value);
    find('errors').replaceChildren();
    if(map.areas.some(a=>a!==old&&a.id===value.id)) {error('다른 구역과 같은 ID입니다. 다른 ID를 입력하세요.');return;}
    try {value.cells=value.id==='yard'?resizeCells(old.cells,value.rows,value.columns):[];} catch(e) {error(e.message);return;}
    map.areas[map.areas.indexOf(old)]=value;selected=value.id;dirty();render();
  });
  properties.addEventListener('input',()=>{pending=true;dirty();find('status').textContent='속성 입력 중 · 구역 변경 적용을 먼저 누르세요.';});
  find('add').addEventListener('click',()=>{
    try {map=addArea(map);selected=map.areas.at(-1).id;dirty();render();} catch(e) {error(e.message);}
  });
  find('delete').addEventListener('click',()=>{
    if(selected==='yard') return;
    map={areas:map.areas.filter(a=>a.id!==selected)};selected='yard';dirty();render();
  });
  find('reset').addEventListener('click',()=>{map=structuredClone(initial);selected=map.areas[0].id;dirty();render();});
  root.querySelectorAll('[data-map-view]').forEach(button=>button.addEventListener('click',()=>{
    if(button.dataset.mapView==='fit')renderer?.fit();else renderer?.view(button.dataset.mapView);
  }));
  function lock(value) {
    busy=value;root.setAttribute('aria-busy',String(value));
    root.querySelectorAll('button,input,select').forEach(control=>{control.disabled=value;});
    if(!value){selection();find('save').disabled=!fingerprint;}
  }
  async function submit(url,body) {
    const response=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':publish.elements.csrfmiddlewaretoken.value},body:JSON.stringify(body)});
    if(response.redirected||!response.headers.get('content-type')?.includes('application/json')) throw new Error('로그인 상태 또는 요청 권한을 확인한 뒤 다시 시도하세요.');
    const result=await response.json();
    if(!response.ok) throw new Error((result.errors||['요청을 처리하지 못했습니다.']).join(' / '));
    return result;
  }
  function summary(result) {
    const content=find('summary');content.replaceChildren();
    const entries=[['추가 구역',result.added_areas],['삭제 구역',result.removed_areas],['변경 구역',result.changed_areas]];
    for(const [label,values] of entries){const p=document.createElement('p');p.textContent=`${label}: ${values?.join(', ')||'없음'}`;content.append(p);}
    for(const [type,values] of Object.entries(result.removed_references||{})) {
      const p=document.createElement('p');p.textContent=`상속 제외 ${type}: ${values.length}개`;content.append(p);
    }
    const note=document.createElement('p');note.textContent='새 칸의 도달 가능성을 승인하지 않습니다. 등록 후 새 기준으로 전체 배치를 관측하세요.';content.append(note);
  }
  find('validate').addEventListener('click',async()=>{
    if(pending){error('선택 구역의 입력을 먼저 적용하세요.');properties.querySelector('button[type=submit]').focus();return;}
    find('errors').replaceChildren();lock(true);find('status').textContent='서버에서 초안을 검증하고 있습니다…';
    try {
      const result=await submit(data.validate_url,{map});
      map=result.map;fingerprint=result.fingerprint;summary(result.summary);render();
      find('status').textContent='초안 검증 완료 · 새 기준으로 등록할 수 있습니다.';
    } catch(e) {fingerprint=null;error(e.message);find('status').textContent='검증 실패 · 오류를 수정한 뒤 다시 검증하세요.';}
    finally {lock(false);}
  });
  publish.addEventListener('submit',async event=>{
    event.preventDefault();if(busy||!fingerprint||!publish.reportValidity())return;
    const name=publish.elements.name.value.trim(), calibration_ref=publish.elements.calibration_ref.value.trim();
    find('errors').replaceChildren();lock(true);find('status').textContent='새 설치 기준을 등록하고 있습니다…';
    try {const result=await submit(data.save_url,{map,fingerprint,name,calibration_ref});location.assign(result.redirect);}
    catch(e) {error(e.message);find('status').textContent='등록하지 못했습니다. 오류를 확인하세요.';lock(false);}
  });
  window.addEventListener('pagehide',()=>renderer?.dispose(),{once:true});
  root.hidden=false;render();
}
