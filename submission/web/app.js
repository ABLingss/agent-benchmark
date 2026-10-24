const $ = selector => document.querySelector(selector);
const statuses = {uploaded:'已收到 · 待验收',queued:'已排队',running:'正在验收',passed:'测试全通过',failed:'部分未通过',error:'运行异常'};
const taskNames = {gateway:'推理网关',frontend:'点餐结算页'};
let active = null, uploading = false, accepting = true;
let recent = [];
try { recent = JSON.parse(localStorage.getItem('benchmark-receipts') || '[]'); if (!Array.isArray(recent)) recent = []; } catch {}
const code = new URLSearchParams(location.search).get('code');
if (code) $('#exam-code').value = code;
function message(text, success=false) { $('#form-message').textContent=text; $('#form-message').classList.toggle('success',success); }
async function api(path, options={}) {
  const response=await fetch(path,options);const data=await response.json();
  if(!response.ok) throw new Error(data.error || '操作未完成，请重试。');return data;
}
function receiptURL(item) { return `${location.origin}/#${new URLSearchParams({id:item.id,token:item.receipt})}`; }
async function copy(text) {
  if(navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const input=document.createElement('textarea');input.value=text;document.body.append(input);input.select();document.execCommand('copy');input.remove();
}
function showResults(container,summary) {
  container.replaceChildren();if(!summary) return;
  const score=document.createElement('div');score.className='result-score';score.textContent=summary.auto_score;
  const max=document.createElement('small');max.textContent=` / ${summary.auto_max} 自动分`;score.append(max);container.append(score);
  for(const group of summary.groups || []) {
    const row=document.createElement('div');row.className='result-row';const name=document.createElement('span');name.textContent=group.group;
    const value=document.createElement('strong');value.textContent=`${group.passed} / ${group.total} 通过`;row.append(name,value);container.append(row);
  }
  const note=document.createElement('p');note.className='result-note';note.textContent=`另有 ${summary.manual_max} 分由面试官人工评价。`;container.append(note);
}
function showReceipt(row) {
  $('#receipt-empty').hidden=true;$('#receipt').hidden=false;
  $('#receipt-name').textContent=row.candidate;
  $('#receipt-status').textContent=statuses[row.status] || row.status;$('#receipt-status').className=`badge ${row.status}`;
  $('#receipt-meta').textContent=`${taskNames[row.task]} · ${new Date(row.created_at).toLocaleString('zh-CN')}`;
  $('#receipt-id').textContent=`回执 ${row.id}`;$('#receipt-error').textContent=row.error || '';
  showResults($('#receipt-results'),row.summary);
}
function renderRecent() {
  $('#recent-panel').hidden=!recent.length;$('#recent-list').replaceChildren();
  for(const item of recent.slice(0,5)) {
    const button=document.createElement('button');button.type='button';button.className='recent-item';
    const name=document.createElement('strong');name.textContent=`${item.candidate} · ${taskNames[item.task]}`;
    const time=document.createElement('span');time.textContent=new Date(item.created_at).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
    button.append(name,time);button.onclick=()=>activate(item);$('#recent-list').append(button);
  }
}
function activate(item) {
  active={id:item.id,receipt:item.receipt};history.replaceState(null,'',receiptURL(active));refreshReceipt();
}
async function refreshReceipt() {
  if(!active || document.hidden) return;
  const current=active;
  try {
    const data=await api(`/api/submissions/${current.id}`,{headers:{'X-Receipt-Token':current.receipt}});
    if(active===current) showReceipt(data.submission);
  } catch(error) { $('#receipt-error').textContent=error.message;$('#receipt-empty').hidden=true;$('#receipt').hidden=false; }
}
function fileChanged() {
  const file=$('#archive').files[0];$('#file-label').textContent=file ? `${file.name} · ${(file.size/1024/1024).toFixed(2)} MB` : '点击选择，或拖入 ZIP 文件';
}
$('#archive').addEventListener('change',fileChanged);
for(const event of ['dragenter','dragover']) $('#dropzone').addEventListener(event,e=>{e.preventDefault();$('#dropzone').classList.add('dragover');});
for(const event of ['dragleave','drop']) $('#dropzone').addEventListener(event,e=>{e.preventDefault();$('#dropzone').classList.remove('dragover');});
$('#dropzone').addEventListener('drop',event=>{if(event.dataTransfer.files.length){$('#archive').files=event.dataTransfer.files;fileChanged();}});
$('#submission-form').addEventListener('submit',async event=>{
  event.preventDefault();if(uploading) return;
  const file=$('#archive').files[0];
  if(!file || !file.name.toLowerCase().endsWith('.zip')) {message('请选择ZIP格式的代码压缩包。');return;}
  if(file.size>20*1024*1024){message('文件超过20MB，请移除依赖和缓存后重新压缩。');return;}
  uploading=true;$('#submit-button').disabled=true;$('#submit-button').textContent='正在上传…';message('');
  try {
    const query=new URLSearchParams({candidate:$('#candidate').value.trim(),task:$('#task').value,filename:file.name});
    const data=await api(`/api/submit?${query}`,{method:'POST',headers:{'Content-Type':'application/zip','X-Exam-Code':$('#exam-code').value.trim()},body:file});
    const item={...data.submission,receipt:data.receipt};
    recent=[item,...recent].slice(0,10);try{localStorage.setItem('benchmark-receipts',JSON.stringify(recent));}catch{}
    renderRecent();activate(item);showReceipt(data.submission);message('提交成功，请保存右侧的回执链接。',true);
  } catch(error) {message(error.message || '上传失败，请检查网络后重试。');}
  finally {uploading=false;$('#submit-button').disabled=!accepting;$('#submit-button').textContent='提交作品 ↗';}
});
$('#copy-receipt').onclick=async()=>{if(!active)return;try{await copy(receiptURL(active));$('#copy-receipt').textContent='回执链接已复制';setTimeout(()=>$('#copy-receipt').textContent='复制回执链接',2000);}catch{message('复制失败，可直接保存当前页面地址。');}};
async function status() {
  try {const data=await api('/api/status');accepting=data.accepting;$('#submit-button').disabled=uploading||!accepting;if(!accepting)message('面试官已暂停接收提交。');}catch{}
}
const fragment=new URLSearchParams(location.hash.slice(1));
if(fragment.get('id') && fragment.get('token')) active={id:fragment.get('id'),receipt:fragment.get('token')};
else if(recent.length) active={id:recent[0].id,receipt:recent[0].receipt};
renderRecent();refreshReceipt();status();setInterval(refreshReceipt,3000);setInterval(status,10000);
