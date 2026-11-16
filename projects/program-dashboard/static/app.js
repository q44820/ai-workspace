const $ = id => document.getElementById(id);
let snapshot = null, includeOld = false;
const states = {running:'运行中',scheduled:'定时启用',stopped:'已停止',error:'异常',manual:'手动／归档',unknown:'待核验'};
function element(tag, text, className) { const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e; }
function dateText(value) { return value ? new Date(value).toLocaleString('zh-CN',{hour12:false}) : '无记录'; }
function render() {
 if(!snapshot)return;
 const query=$('search').value.trim().toLowerCase(), state=$('status').value, loc=$('location').value;
 const all=snapshot.items, counts={};for(const row of all)counts[row.state]=(counts[row.state]||0)+1;
 $('summary').replaceChildren(...Object.entries(states).map(([key,label])=>{const box=element('button',undefined,'stat '+key);box.append(element('strong',String(counts[key]||0)),element('span',label));box.onclick=()=>{$('status').value=key;render();};return box;}));
 const filtered=all.filter(row=>{
  if(state&&row.state!==state)return false;
  if(loc&&!row.location.includes(loc))return false;
  if(!includeOld&&!state&&row.state==='stopped'&&['OpenClaw内部任务','Hermes内部任务'].includes(row.category))return false;
  return !query||[row.name,row.purpose,row.category,row.usage].join(' ').toLowerCase().includes(query);
 });
 const groups=new Map();for(const row of filtered){if(!groups.has(row.category))groups.set(row.category,[]);groups.get(row.category).push(row);}
 $('groups').replaceChildren();
 for(const [name,rows] of groups){
  const section=element('section',undefined,'group');section.append(element('h2',name+' · '+rows.length));const grid=element('div',undefined,'grid');
  for(const row of rows){const card=element('article',undefined,'card');const top=element('div',undefined,'card-top');top.append(element('h3',row.name),element('span',row.label||states[row.state]||'待核验','badge '+row.state));card.append(top,element('div',row.location+(row.schedule?' · '+row.schedule:''),'sub'),element('p',row.purpose,'purpose'),element('p',row.detail||'','detail'));
   if(row.usage){const use=element('div',undefined,'usage');use.append(element('strong','如何使用／更新'),element('p',row.usage));card.append(use);}
   const bottom=element('div',undefined,'card-bottom');if(row.code&&row.code.startsWith('https://github.com/q44820/ai-workspace/')){const a=element('a','代码与中文说明 ↗');a.href=row.code;a.target='_blank';a.rel='noopener noreferrer';bottom.append(a);}if(row.checked_at)bottom.append(element('span','核验 '+dateText(row.checked_at)));card.append(bottom);grid.append(card);
  }section.append(grid);$('groups').append(section);
 }
 $('empty').classList.toggle('hidden',filtered.length>0);
 $('updated').textContent='共 '+all.length+' 项 · 更新 '+dateText(snapshot.generated_at);
 const sync=snapshot.local_sync;$('sync').textContent=sync.label+(sync.captured_at?' · 采集 '+dateText(sync.captured_at):'')+'。服务器状态实时检查，本机状态分开同步。';$('sync').classList.toggle('warn',sync.state!=='fresh');
 $('scope').textContent='覆盖范围：'+snapshot.scope;$('privacy').textContent=snapshot.privacy;
 $('show-active').classList.toggle('selected',!includeOld);$('show-all').classList.toggle('selected',includeOld);
}
async function refresh(){try{const r=await fetch('api/status',{cache:'no-store',credentials:'same-origin'});if(!r.ok)throw new Error('HTTP '+r.status);snapshot=await r.json();$('error').classList.add('hidden');render();}catch(e){$('error').textContent='状态读取失败，请检查网络或稍后刷新。页面旧数据不代表当前状态。';$('error').classList.remove('hidden');}}
for(const id of ['search','status','location'])$(id).addEventListener('input',render);
$('show-active').onclick=()=>{includeOld=false;render();};$('show-all').onclick=()=>{includeOld=true;render();};$('refresh').onclick=refresh;
refresh();setInterval(refresh,30000);
