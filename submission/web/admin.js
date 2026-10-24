const $=selector=>document.querySelector(selector);
const statuses={uploaded:'待运行',queued:'已排队',running:'运行中',passed:'全通过',failed:'未全通过',error:'运行异常'};
const taskNames={gateway:'推理网关',frontend:'点餐结算页'};
let settings=null, rows=[], selected=null, fingerprint='';
async function api(path,options={}) {
  const response=await fetch(path,options);const data=await response.json();
  if(!response.ok) throw new Error(data.error||'操作未完成。');return data;
}
const post=(path,data={})=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function message(text,success=false){$('#admin-message').textContent=text;$('#admin-message').classList.toggle('success',success);}
async function copy(text){
  if(navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const input=document.createElement('textarea');input.value=text;document.body.append(input);input.select();document.execCommand('copy');input.remove();
}
function publicURL() {
  const base=settings.tunnel.url || `http://127.0.0.1:${settings.public_port}`;
  const url=new URL(base);url.searchParams.set('code',settings.exam_code);return url.toString();
}
function renderStatus(value){
  settings=value;const url=publicURL();if($('#public-url').value!==url)$('#public-url').value=url;
  $('#exam-code').textContent=value.exam_code;
  $('#accepting').textContent=value.accepting?'暂停接收':'恢复接收';
  for(const task of ['gateway','frontend']){
    const node=$(`#${task}-ready`);node.textContent=value.ready[task]?'验收就绪':'尚未就绪';node.className=value.ready[task]?'ready-text':'missing-text';
  }
  $('#environment-help').textContent=value.ready.gateway&&value.ready.frontend?'两题均可在本机运行验收，无需模型API。':'请把面试官专用包解压到仓库旁边的agent-benchmark-private目录；前端还需安装其中的Node依赖与Playwright浏览器。';
  const labels={off:'本地入口',starting:'外网连接中',ready:'外网已连接',error:'外网未连接'};
  $('#tunnel-status').textContent=labels[value.tunnel.status]||value.tunnel.status;
  $('#tunnel-status').className=`badge ${value.tunnel.status==='ready'?'ready':value.tunnel.status==='error'?'error':''}`;
  $('#tunnel-start').disabled=value.tunnel.status==='starting';
  $('#tunnel-start').hidden=value.tunnel.status==='ready';$('#tunnel-stop').hidden=!['starting','ready'].includes(value.tunnel.status);
  $('#tunnel-message').textContent=value.tunnel.error || (value.tunnel.status==='starting'?'正在生成并连接外网地址，通常需要几秒至几十秒…':'');
}
function cell(text){const node=document.createElement('td');node.textContent=text;return node;}
function renderRows(){
  $('#stat-total').textContent=rows.length;$('#stat-pending').textContent=rows.filter(x=>['uploaded','queued'].includes(x.status)).length;
  $('#stat-running').textContent=rows.filter(x=>x.status==='running').length;$('#stat-passed').textContent=rows.filter(x=>x.status==='passed').length;
  $('#admin-empty').hidden=rows.length>0;$('#submission-rows').replaceChildren();
  for(const row of rows){
    const line=document.createElement('tr');line.dataset.id=row.id;
    const identity=document.createElement('td');const name=document.createElement('strong');name.textContent=row.candidate;const id=document.createElement('small');id.textContent=row.id.slice(0,10);identity.append(name,id);
    const status=document.createElement('td');const badge=document.createElement('span');badge.className=`badge ${row.status}`;badge.textContent=statuses[row.status];status.append(badge);
    const actions=document.createElement('td');const group=document.createElement('div');group.className='row-actions';
    const run=document.createElement('button');run.type='button';run.className='primary';run.textContent=['uploaded','queued','running'].includes(row.status)?'运行测试':'重新测试';
    run.disabled=['queued','running'].includes(row.status)||!settings?.ready[row.task];
    run.onclick=async()=>{run.disabled=true;try{await post(`/api/run/${row.id}`);message('已加入验收队列。',true);await refresh();}catch(error){message(error.message);run.disabled=false;}};
    const details=document.createElement('button');details.type='button';details.className='text-button';details.textContent='详情';details.onclick=()=>showDetail(row.id,true);
    const download=document.createElement('a');download.href=`/api/submissions/${row.id}/archive`;download.textContent='下载';
    group.append(run,details,download);actions.append(group);
    line.append(identity,cell(taskNames[row.task]),cell(new Date(row.created_at).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'})),status,cell(row.summary?`${row.summary.auto_score} / ${row.summary.auto_max}`:'—'),actions);
    $('#submission-rows').append(line);
  }
}
function resultDOM(summary){
  const container=$('#detail-results');container.replaceChildren();if(!summary)return;
  const score=document.createElement('div');score.className='result-score';score.textContent=`${summary.auto_score} / ${summary.auto_max} 自动分`;container.append(score);
  for(const group of summary.groups||[]){
    const line=document.createElement('div');line.className='result-row';const title=document.createElement('span');title.textContent=group.group;
    const value=document.createElement('strong');value.textContent=`${group.passed}/${group.total} 通过 · ${group.score}/${group.max} 分`;line.append(title,value);container.append(line);
  }
  const note=document.createElement('p');note.className='result-note';note.textContent=`人工评价另计 ${summary.manual_max} 分。`;container.append(note);
}
async function showDetail(id,scroll=false){
  selected=id;const row=rows.find(x=>x.id===id);if(!row)return;
  $('#detail-panel').hidden=false;$('#detail-title').textContent=`${row.candidate} · ${taskNames[row.task]} · ${statuses[row.status]}`;
  resultDOM(row.summary);$('#detail-error').textContent=row.error||'';
  try{const response=await fetch(`/api/submissions/${id}/log`);if(response.ok)$('#detail-log').textContent=await response.text();}catch{$('#detail-log').textContent='日志暂时不可用。';}
  if(scroll)$('#detail-panel').scrollIntoView({behavior:'smooth',block:'nearest'});
}
async function refresh(){
  if(document.hidden)return;
  try{
    const [status,data]=await Promise.all([api('/api/status'),api('/api/submissions')]);renderStatus(status);
    const next=JSON.stringify(data.submissions)+JSON.stringify(status.ready);
    rows=data.submissions;if(next!==fingerprint){fingerprint=next;renderRows();if(selected)showDetail(selected);}
  }catch(error){message(error.message);}
}
$('#refresh').onclick=()=>{fingerprint='';refresh();};$('#close-detail').onclick=()=>{selected=null;$('#detail-panel').hidden=true;};
$('#copy-url').onclick=async()=>{try{await copy(publicURL());message(settings.tunnel.url?'候选人外网链接已复制。':'本地链接已复制；外网使用请先生成外网链接。',true);}catch{message('复制失败，请手动复制入口地址。');}};
$('#copy-code').onclick=async()=>{try{await copy(settings.exam_code);message('提交口令已复制。',true);}catch{message('复制失败，请手动复制口令。');}};
$('#tunnel-start').onclick=async()=>{try{await post('/api/tunnel/start');await refresh();}catch(error){message(error.message);}};
$('#tunnel-stop').onclick=async()=>{try{await post('/api/tunnel/stop');await refresh();}catch(error){message(error.message);}};
$('#accepting').onclick=async()=>{try{await post('/api/accepting',{accepting:!settings.accepting});await refresh();}catch(error){message(error.message);}};
$('#export-results').onclick=()=>{const blob=new Blob([JSON.stringify({exported_at:new Date().toISOString(),submissions:rows},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=`benchmark-results-${Date.now()}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
refresh();setInterval(refresh,2500);
