'use strict';
const el=id=>document.getElementById(id), esc=v=>String(v??'未知').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const owned=new Set(['Binance','OKX','Bybit','Bitget','Gate','Hyperliquid','Entropy','Lighter']);
// Pure time functions: accept only explicit timezone strings for source dates.
function parseZoned(value){
 if(typeof value!=='string'||!/(Z|[+-]\d{2}:?\d{2})$/i.test(value)||!value.includes('T'))return null;
 const n=Date.parse(value);return Number.isFinite(n)?n:null;
}
function getNow(){const v=typeof window.ARB_NOW==='function'?window.ARB_NOW():window.ARB_NOW;return v==null?Date.now():new Date(v).getTime();}
function timeState(value,now,kind){const t=parseZoned(value);return t==null?'unknown':(kind==='age'?now-t>90000:now>=t)?'expired':'fresh';}
function timeLabel(value,now,kind){const status=timeState(value,now,kind);return status==='unknown'?'未知':kind==='age'?(status==='expired'?'陈旧 / 过期缓存':'采集年龄未超过90秒'):(status==='expired'?'下次资金费时间已过期；不要按原费率判断':'未过期');}
function isComparable(r){return ['verified','candidate'].includes(r.status);}
function matchesCoin(coin,query){return !query.trim()||coin.toLowerCase()===query.trim().toLowerCase();}
function sourceStale(s){s=s||{};const t=parseZoned(s.last_success),q=parseZoned(s.quote_time);return !!s.error||t==null||getNow()-Math.min(t,q??t)>(s.stale_after_s??90)*1000;}
function rowStale(r){return sourceStale(r.long)||sourceStale(r.short);}
function rowStatus(r){return r.comparison_status||r.status;}
function finiteMetric(v){return typeof v==='number'&&Number.isFinite(v);}
function fundingNet(r,fee,N,hours){return isComparable(r)&&finiteMetric(r.hourly_diff)?r.hourly_diff*hours*N-4*fee*N:NaN;}
function fundingHourly(r){return isComparable(r)&&finiteMetric(r.hourly_diff)?r.hourly_diff:NaN;}
function spreadAfterFee(r,fee,N){return isComparable(r)&&finiteMetric(r.basis)?r.basis*N-4*fee*N:NaN;}
function spreadBasis(r){return isComparable(r)&&finiteMetric(r.basis)?r.basis:NaN;}
function sortGroup(r,metric){
 const ok=isComparable(r)&&finiteMetric(metric),stale=rowStale(r),status=rowStatus(r);
 if(ok&&status==='verified'&&!stale)return 0;
 if(ok&&status==='candidate'&&!stale)return 1;
 if(ok&&stale)return 2;
 return 3;
}
function metricFor(sortId,fee,N,hours){
 if(sortId==='net_desc'||sortId==='net_asc')return r=>fundingNet(r,fee,N,hours);
 if(sortId==='hourly_desc'||sortId==='hourly_asc')return r=>fundingHourly(r);
 if(sortId==='spread_desc'||sortId==='spread_asc')return r=>spreadAfterFee(r,fee,N);
 if(sortId==='basis_desc'||sortId==='basis_asc')return r=>spreadBasis(r);
 return ()=>NaN;
}
function sortRows(rows,metricFn,desc){
 return rows.map((r,i)=>({r,i})).sort((a,b)=>{
  const ma=metricFn(a.r),mb=metricFn(b.r),ga=sortGroup(a.r,ma),gb=sortGroup(b.r,mb);
  if(ga!==gb)return ga-gb;
  if(ga===3||ma===mb)return a.i-b.i;
  return desc?mb-ma:ma-mb;
 }).map(x=>x.r);
}
function applyFilters(rows,page,opts){
 const q=opts.query||'';
 return rows.filter(r=>r.strategy===page&&matchesCoin(r.coin,q)&&(!opts.ownedOnly||[r.long_platform,r.short_platform].every(p=>owned.has(p)))&&(!opts.platform||[r.long_platform,r.short_platform].includes(opts.platform))&&(opts.status?r.status===opts.status:isComparable(r))&&(!opts.period||(opts.period==='unknown'?[r.long?.hours,r.short?.hours].some(v=>v==null):[r.long?.hours,r.short?.hours].some(v=>v===Number(opts.period)))));
}
function presentRows(rows,page,opts,sortId,fee,N,hours,limit){
 const ranked=sortRows(applyFilters(rows,page,opts),metricFor(sortId,fee,N,hours),!String(sortId).endsWith('_asc'));
 return limit==null?ranked:ranked.slice(0,limit);
}
function sortChoices(page){
 return page==='funding'?[['net_desc','预计净收益 高→低'],['net_asc','预计净收益 低→高'],['hourly_desc','每小时费率差 高→低'],['hourly_asc','每小时费率差 低→高']]:page==='spread'?[['spread_desc','扣费后价差 高→低'],['spread_asc','扣费后价差 低→高'],['basis_desc','原始 basis 高→低'],['basis_asc','原始 basis 低→高']]:[];
}
function rowKey(r){return [r.strategy,r.coin,r.long_platform,r.short_platform].map(v=>String(v??'')).join('\t');}
function dash(v){return finiteMetric(v)?null:'—';}
function formatPct(v,digits){return finiteMetric(v)?((v*100).toFixed(digits==null?4:digits)+'%'):'—';}
function formatNum(v,digits){return finiteMetric(v)?v.toFixed(digits==null?4:digits):'—';}
function signedClass(v){return !finiteMetric(v)?'num-unknown':v>0?'num-up':v<0?'num-down':'num-zero';}
function signedPct(v,digits){if(!finiteMetric(v))return '—';const t=formatPct(v,digits);return v>0&&t[0]!=='+'&&t[0]!=='-'?'+'+t:t;}
function signedNum(v,digits){if(!finiteMetric(v))return '—';const t=formatNum(v,digits);return v>0&&t[0]!=='+'&&t[0]!=='-'?'+'+t:t;}
function displayFundingNet(r,fee,N,hours){const v=fundingNet(r,fee,N,hours);return finiteMetric(v)?formatNum(v,4):'不可比';}
function displaySpreadFee(r,fee,N){const v=spreadAfterFee(r,fee,N);return finiteMetric(v)?formatNum(v,4):'不可比';}
function overviewFrom(rows,page,fee,N,hours){
 const counts={total:rows.length,verified:0,candidate:0,stale:0,incomparable:0,comparable:0};
 const vals=[];
 for(const r of rows){
  const st=rowStatus(r);
  if(st==='verified')counts.verified++;
  else if(st==='candidate')counts.candidate++;
  if(rowStale(r))counts.stale++;
  const m=page==='funding'?fundingNet(r,fee,N,hours):page==='spread'?spreadAfterFee(r,fee,N):NaN;
  if(finiteMetric(m)){vals.push(m);counts.comparable++;}
  else counts.incomparable++;
 }
 return {counts,max:vals.length?Math.max(...vals):null,min:vals.length?Math.min(...vals):null};
}
function statusLabels(){return {verified:'已核对',candidate:'未核实候选',mixed:'口径不同不可比',anomaly:'价格异常',identity_unverified:'无法核对',unknown:'周期未知'};}
function badgeHtml(r){
 const st=rowStatus(r),labels=statusLabels(),out=[];
 out.push('<span class="badge '+esc(st)+'">'+esc(labels[st]||st)+'</span>');
 if(st==='candidate')out.push('<span class="badge candidate">不是可执行</span>');
 if(rowStale(r))out.push('<span class="badge stale">陈旧</span>');
 return '<div class="badges">'+out.join('')+'</div>';
}
function sortArrow(sortId,col){
 const map={hourly:['hourly_desc','hourly_asc'],net:['net_desc','net_asc'],spread:['spread_desc','spread_asc'],basis:['basis_desc','basis_asc']};
 const pair=map[col];if(!pair)return '';
 if(sortId===pair[0])return ' ↓';if(sortId===pair[1])return ' ↑';return '';
}
function headerCell(label,col,sortId){return '<th class="sortable'+(col==='coin'?' coin':'')+(label.includes('差')||label.includes('收益')||label.includes('basis')||label.includes('价')?' num':'')+'" data-col="'+esc(col)+'" scope="col">'+esc(label)+sortArrow(sortId,col)+'</th>';}
const bj=t=>parseZoned(t)==null?'未知':new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'short',timeStyle:'medium',hour12:false}).format(new Date(t))+' 北京时间';
const pct=v=>v==null?'未知':(v*100).toFixed(6)+'%';
const detail=x=>'<details><summary>原始字段、时间与口径</summary><pre>'+esc(JSON.stringify(x,null,2))+'</pre></details>';
const PAGE_SIZE=50;
let state,page='funding',sortMemory={funding:'net_desc',spread:'spread_desc'},sortPage=null;
let expanded=new Set(),listPageMemory={funding:1,spread:1};
function sourceTiming(s){return '源采集时间 '+bj(s.last_success)+'；行情时点 '+bj(s.quote_time)+'；实际刷新周期 '+esc(s.actual_interval_s??s.target_interval_s??'未知')+' 秒；目标 '+esc(s.target_interval_s??'未知')+' 秒；下次允许 '+bj(s.next_attempt)+'；'+(sourceStale(s)?'陈旧 / 刷新失败':'缓存有效');}
function periodText(m){return m==null||m.hours==null?'未知':m.hours+'h';}
function rateLine(m,side){
 m=m||{};
 const name=esc(m.exchange||'未知');
 const rate=finiteMetric(m.rate)?formatPct(m.rate,4):'—';
 return '<span class="'+(side==='long'?'side-long':'side-short')+'">'+(side==='long'?'多 ':'空 ')+name+'</span><span class="muted">'+rate+' / '+esc(periodText(m))+'</span>';
}
function settleShort(value,now){
 const s=timeState(value,now,'next');
 return s==='unknown'?'未知':s==='expired'?'已过期':'未过期';
}
function nextSettle(r){
 const times=[r.long&&r.long.next_iso,r.short&&r.short.next_iso].filter(Boolean);
 if(!times.length)return '—';
 const stamped=times.map(t=>({t,n:parseZoned(t)})).filter(x=>x.n!=null).sort((a,b)=>a.n-b.n);
 const pick=stamped[0]||{t:times[0]};
 return '<span>'+esc(bj(pick.t))+'</span><span class="muted">'+esc(settleShort(pick.t,getNow()))+'</span>';
}
function coinCell(r){
 return '<td class="coin"><div class="coin-name">'+esc(r.coin)+'</div>'+badgeHtml(r)+'</td>';
}
function updatedAt(r){
 const times=[r.long&&r.long.last_success,r.short&&r.short.last_success].filter(Boolean);
 if(!times.length)return '—';
 return esc(bj(times[0]))+(times[1]&&times[1]!==times[0]?'<span class="muted"> / '+esc(bj(times[1]))+'</span>':'');
}
function fundingDetail(r,fee,N,hours){
 const comparison=rowStatus(r),gross=r.hourly_diff==null?null:r.hourly_diff*hours*N,net=fundingNet(r,fee,N,hours);
 const calculation=comparison==='mixed'?'不同口径算术差，不能扣费后当作净利':comparison==='unknown'?'周期未知，不能扣费后当作净利':'算术收入 '+(gross==null?'未知':gross.toFixed(4))+'；示例四笔费用 '+(4*fee*N).toFixed(4)+'；预计净收益（估算，不保证可赚） '+(finiteMetric(net)?net.toFixed(4):'不可比')+'（不是可执行净利润，未计盘口深度）';
 const leg=m=>'<p>'+esc(m.exchange)+' · '+esc(m.rate_kind||'unknown')+' · '+esc(m.meaning)+'；原始费率 '+pct(m.rate)+'（小数 '+esc(m.rate)+'）；周期 '+esc(m.hours==null?'未知':m.hours+'h')+'；独立指示/预测 '+pct(m.indicative)+'；下一结算 '+bj(m.next_iso)+'；'+timeLabel(m.next_iso,getNow(),'next')+'</p>';
 return '<p>'+esc(r.direction||'')+'</p><p>每小时算术差 '+pct(r.hourly_diff)+'</p>'+leg(r.long||{})+leg(r.short||{})+'<p>'+esc(r.conflict||'')+'</p><p>'+calculation+'</p><p>状态：'+esc((statusLabels()[comparison]||comparison))+'；多腿 '+(sourceStale(r.long)?'陈旧 / 刷新失败':'缓存有效')+'；空腿 '+(sourceStale(r.short)?'陈旧 / 刷新失败':'缓存有效')+'</p><small>最后成功：多 '+bj(r.long&&r.long.last_success)+'；空 '+bj(r.short&&r.short.last_success)+'</small>'+detail(r);
}
function spreadDetail(r,fee,N){
 const spreadFee=spreadAfterFee(r,fee,N),comparison=rowStatus(r);
 return '<p>方向：多现货 / 空永续；原始 basis '+pct(r.basis)+'；扣费后价差（示例） '+(finiteMetric(spreadFee)?spreadFee.toFixed(4):'不可比')+'（同一单边费率×四笔，不是可成交净利，也不是均值回归收益）；现货 '+esc(r.long&&r.long.price_field)+' '+esc(r.long&&r.long.price)+' / 永续 '+esc(r.short&&r.short.price_field)+' '+esc(r.short&&r.short.price)+'；未计盘口深度，不是可成交净价差</p><p>状态：'+esc((statusLabels()[comparison]||comparison))+'；现货腿 '+(sourceStale(r.long)?'陈旧 / 刷新失败':'缓存有效')+'；永续腿 '+(sourceStale(r.short)?'陈旧 / 刷新失败':'缓存有效')+'</p><small>最后成功：现货 '+bj(r.long&&r.long.last_success)+'；永续 '+bj(r.short&&r.short.last_success)+'</small>'+detail(r);
}
function fundingRowHtml(r,fee,N,hours,open){
 const key=rowKey(r),net=fundingNet(r,fee,N,hours),hourly=fundingHourly(r);
 return '<tr class="main" data-key="'+esc(key)+'">'+coinCell(r)+'<td><div class="stack">'+rateLine(r.long,'long')+rateLine(r.short,'short')+'</div></td><td class="num '+signedClass(hourly)+'">'+esc(finiteMetric(hourly)?signedPct(hourly,4):'—')+'</td><td class="num '+signedClass(net)+'"><div class="stack"><span class="value">'+esc(finiteMetric(net)?signedNum(net,4):'不可比')+'</span><span class="muted">估算</span></div></td><td><div class="stack">'+nextSettle(r)+'</div></td><td class="col-action"><button type="button" class="expand" data-key="'+esc(key)+'" aria-expanded="'+(open?'true':'false')+'">'+(open?'收起':'展开')+'</button></td></tr><tr class="detail"'+(open?'':' hidden')+' data-for="'+esc(key)+'"><td colspan="6">'+fundingDetail(r,fee,N,hours)+'</td></tr>';
}
function spreadRowHtml(r,fee,N,open){
 const key=rowKey(r),basis=spreadBasis(r),adj=spreadAfterFee(r,fee,N);
 const spot=r.long&&r.long.price,perp=r.short&&r.short.price;
 return '<tr class="main" data-key="'+esc(key)+'">'+coinCell(r)+'<td><div class="stack"><span class="side-long">现货 '+esc(r.long_platform)+'</span><span class="side-short">永续 '+esc(r.short_platform)+'</span></div></td><td class="num"><div class="stack"><span>'+esc(spot==null?'—':String(spot))+'</span><span>'+esc(perp==null?'—':String(perp))+'</span></div></td><td class="num '+signedClass(basis)+'">'+esc(finiteMetric(basis)?signedPct(basis,4):'—')+'</td><td class="num '+signedClass(adj)+'"><div class="stack"><span class="value">'+esc(finiteMetric(adj)?signedNum(adj,4):'不可比')+'</span><span class="muted">示例价差</span></div></td><td><div class="stack">'+updatedAt(r)+'</div></td><td class="col-action"><button type="button" class="expand" data-key="'+esc(key)+'" aria-expanded="'+(open?'true':'false')+'">'+(open?'收起':'展开')+'</button></td></tr><tr class="detail"'+(open?'':' hidden')+' data-for="'+esc(key)+'"><td colspan="7">'+spreadDetail(r,fee,N)+'</td></tr>';
}
function tableHtml(page,rows,sortId,fee,N,hours){
 if(page==='funding'){
  const head='<thead><tr>'+['<th class="coin" scope="col">币种</th>','<th scope="col">做多 / 做空</th>',headerCell('每小时费率差','hourly',sortId),headerCell('预计收益','net',sortId),'<th scope="col">下次结算</th>','<th class="col-action" scope="col">详情</th>'].join('')+'</tr></thead>';
  return '<table class="data"><caption class="muted">一行一条资金费路线。预计收益为估算，不是可执行净利。</caption>'+head+'<tbody>'+rows.map(r=>fundingRowHtml(r,fee,N,hours,expanded.has(rowKey(r)))).join('')+'</tbody></table>';
 }
 const head='<thead><tr>'+['<th class="coin" scope="col">币种</th>','<th scope="col">现货 / 永续</th>','<th class="num" scope="col">现货价 / 永续价</th>',headerCell('原始 basis','basis',sortId),headerCell('扣费后价差','spread',sortId),'<th scope="col">更新时间</th>','<th class="col-action" scope="col">详情</th>'].join('')+'</tr></thead>';
 return '<table class="data"><caption class="muted">一行一条现货-永续价差。扣费后价差仅为示例，不是均值回归净利。</caption>'+head+'<tbody>'+rows.map(r=>spreadRowHtml(r,fee,N,expanded.has(rowKey(r)))).join('')+'</tbody></table>';
}
if(el('platform'))owned.forEach(p=>el('platform').innerHTML+='<option>'+p+'</option>');
function filterOptionsFromForm(){return {query:el('search').value,ownedOnly:el('owned').checked,platform:el('platform').value,status:el('status').value,period:el('period').value};}
function syncSortOptions(){
 const wrap=el('sort-wrap'),box=el('sort');
 if(!wrap||!box)return;
 if(page==='sources'){wrap.hidden=true;return;}
 wrap.hidden=false;
 if(sortPage!==page){
  const opts=sortChoices(page),wanted=sortMemory[page]||opts[0][0];
  box.innerHTML=opts.map(([v,l])=>'<option value="'+v+'">'+esc(l)+'</option>').join('');
  box.value=opts.some(o=>o[0]===wanted)?wanted:opts[0][0];
  sortMemory[page]=box.value;
  sortPage=page;
 }
}
function applyTheme(theme){
 const next=theme==='dark'?'dark':'light';
 document.documentElement.setAttribute('data-theme',next);
 try{localStorage.setItem('arb-hub-theme',next);}catch(e){}
 if(el('theme'))el('theme').textContent=next==='dark'?'浅色':'深色';
}
function captureUi(){
 const wrap=el('table-wrap'),active=document.activeElement;
 return {scrollLeft:wrap?wrap.scrollLeft:0,scrollTop:wrap?wrap.scrollTop:0,winY:window.scrollY,focusId:active&&active.id,start:active&&typeof active.selectionStart==='number'?active.selectionStart:null,end:active&&typeof active.selectionEnd==='number'?active.selectionEnd:null};
}
function restoreUi(snap){
 if(!snap)return;
 const wrap=el('table-wrap');
 if(wrap){wrap.scrollLeft=snap.scrollLeft;wrap.scrollTop=snap.scrollTop;}
 if(typeof snap.winY==='number')window.scrollTo(0,snap.winY);
 if(snap.focusId){
  const n=el(snap.focusId);
  if(n&&typeof n.focus==='function'){
   n.focus();
   if(typeof snap.start==='number'&&n.setSelectionRange){try{n.setSelectionRange(snap.start,snap.end);}catch(e){}}
  }
 }
}
function renderOverview(rows,view,fee,N,hours){
 const box=el('overview');if(!box)return;
 if(view==='sources'){box.innerHTML='';return;}
 const o=overviewFrom(rows,view,fee,N,hours),c=o.counts;
 const label=view==='funding'?'可比估算收益':'可比扣费后价差';
 const item=(title,val,cls)=>'<div class="metric"><b>'+esc(title)+'</b><span class="'+(cls||'')+'">'+esc(val)+'</span></div>';
 box.innerHTML=item('匹配路线',String(c.total))+item('已核对',String(c.verified))+item('未核实候选',String(c.candidate))+item('陈旧',String(c.stale))+item('不可比或缺数据',String(c.incomparable))+item(label+'最高',finiteMetric(o.max)?signedNum(o.max,4):'—',signedClass(o.max))+item(label+'最低',finiteMetric(o.min)?signedNum(o.min,4):'—',signedClass(o.min));
}
function renderPager(total,pageNo){
 const box=el('pager');if(!box)return;
 const pages=Math.max(1,Math.ceil(total/PAGE_SIZE));
 const cur=Math.min(Math.max(1,pageNo),pages);
 box.innerHTML='<span>第 '+cur+' / '+pages+' 页，已对全部 '+total+' 条匹配结果排序后再分页（每页 '+PAGE_SIZE+' 条）</span>'+(pages>1?'<button type="button" id="page-prev"'+(cur<=1?' disabled':'')+'>上一页</button><button type="button" id="page-next"'+(cur>=pages?' disabled':'')+'>下一页</button>':'');
}
function renderSources(){
 if(!state||!el('sourceRows'))return;
 const official=[{name:'公开 pulse 摘要',feed:true,last_success:state.cache&&state.cache.last_success,quote_time:state.cache&&state.cache.quote_time,actual_interval_s:state.cache&&state.cache.actual_interval_s,target_interval_s:state.cache&&state.cache.target_interval_s,next_attempt:state.cache&&state.cache.next_attempt,error:state.cache&&state.cache.error,rows:(state.cache&&state.cache.pulse&&state.cache.pulse.prices||[]).length,field_notes:'文档 feed 摘要，best 缺腿不可执行',schedule_notes:''}].concat(state.official_sources||[]);
 const head='<table class="data"><thead><tr><th class="coin">来源</th><th>状态</th><th>最后成功</th><th>行数</th><th>说明</th></tr></thead><tbody>';
 const body=official.map(s=>{
  const stale=sourceStale(s),ok=s.last_success&&!stale;
  return '<tr class="main"><td class="coin">'+esc(s.name|| (s.feed?'公开 pulse 摘要':''))+'</td><td>'+(ok?'<span class="badge verified">缓存有效</span>':stale?'<span class="badge stale">陈旧 / 刷新失败</span>':'<span class="badge stale">未取得数据</span>')+'</td><td>'+esc(bj(s.last_success))+'</td><td class="num">'+esc(String(s.rows??'—'))+'</td><td class="muted">'+esc(sourceTiming(s))+'；'+esc(s.schedule_notes||'')+' '+esc(s.field_notes||'')+(s.error?'；错误 '+esc(s.error):'')+'</td></tr>';
 }).join('');
 const catalog=(state.sources||[]).map(s=>{
  const name=s[0],note=s[0]==='CryptoLabs'?'手动导入/旧快照：实际 '+(state.rows||[]).filter(r=>r.source_url&&String(r.source_url).includes('cryptolabs')).length+' 行；'+s[2]:(s[0]==='Humarb'||s[0]==='Bybit'||s[0]==='ArbTide 状态'?'限制不可接：'+s[2]:s[0]==='ArbTide pulse'?s[2]:'仅入口/SPA 未自动接入（外链）：'+s[2]);
  return '<tr class="main"><td class="coin">'+esc(name)+'</td><td class="muted" colspan="3">'+esc(note)+'</td><td><a href="'+esc(s[1])+'" rel="noreferrer">来源入口</a></td></tr>';
 }).join('');
 el('sourceRows').innerHTML=head+body+catalog+'</tbody></table>';
}
function render(opts){
 const snap=opts&&opts.preserve?captureUi():null;
 el('routes').hidden=page==='sources';el('sources').hidden=page!=='sources';
 document.querySelectorAll('.tabs [data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===page));
 syncSortOptions();
 const title=page==='funding'?'资金费套利':page==='spread'?'价差 / 现期':'来源状态';
 if(el('heading'))el('heading').textContent=page==='sources'?title:title+' · 公开数据汇总';
 const fee=Math.max(0,Number(el('fee').value)||0)/100,N=Math.max(0,Number(el('notional').value)||0),hours=Math.max(0,Number(el('hours').value)||0);
 const sortId=(el('sort')&&el('sort').value)||sortMemory[page]||(page==='spread'?'spread_desc':'net_desc');
 if(page!=='sources')sortMemory[page]=sortId;
 const all=(!state||el('source').value==='manual')?[]:presentRows(state.official_rows,page,filterOptionsFromForm(),sortId,fee,N,hours);
 renderOverview(all,page,fee,N,hours);
 if(page==='sources'){renderSources();if(el('pager'))el('pager').innerHTML='';restoreUi(snap);return;}
 const pages=Math.max(1,Math.ceil(all.length/PAGE_SIZE));
 let pageNo=listPageMemory[page]||1;if(pageNo>pages)pageNo=pages;if(pageNo<1)pageNo=1;listPageMemory[page]=pageNo;
 const slice=all.slice((pageNo-1)*PAGE_SIZE,pageNo*PAGE_SIZE);
 if(el('count'))el('count').textContent='匹配 '+all.length+' 条 / 官方缓存路线 '+(state.official_rows||[]).filter(r=>r.strategy===page).length+' 条；已按当前名义 N、持有小时、费率重算。分组：已核对新鲜 → 候选新鲜 → 陈旧 → 不可比/异常/缺数据，组内再按当前排序；候选未核实，不会改标为已核对。';
 if(el('rows'))el('rows').innerHTML=tableHtml(page,slice,sortId,fee,N,hours);
 renderPager(all.length,pageNo);
 if(el('manual'))el('manual').innerHTML=(!state||el('source').value==='official')?'':(state.rows||[]).filter(r=>r.strategy===page&&matchesCoin(r.coin,el('search').value)&&(!el('owned').checked||[r.long_platform,r.short_platform].every(p=>owned.has(p)))&&(!el('platform').value||[r.long_platform,r.short_platform].includes(el('platform').value))&&(!el('status').value||el('status').value==='stale')&&(!el('period').value||el('period').value==='unknown')).map(r=>'<article class="stale">'+esc(r.coin)+' · 手动/旧快照，过期，无自动更新'+detail(r)+'</article>').join('');
 restoreUi(snap);
}
function initialize(s,opts){state=s;
 if(window['OFFLINE']&&el('stamp'))el('stamp').textContent=window.OFFLINE?'快照北京时间：'+bj(window.SNAPSHOT_TIME):'浏览器读取本地缓存时间：'+bj(new Date().toISOString());
 if(el('pulse'))el('pulse').textContent=state?JSON.stringify({时效:sourceTiming(state.cache),last_success:bj(state.cache.last_success),last_attempt:bj(state.cache.last_attempt),error:state.cache.error,pulse:state.cache.pulse},null,2):'';
 render({preserve:opts&&opts.preserve});
}
function resetListPage(){if(page!=='sources')listPageMemory[page]=1;}
function onFilterChange(id){
 if(id==='sort'&&page!=='sources'&&el('sort'))sortMemory[page]=el('sort').value;
 if(['search','platform','source','period','status','owned','sort'].includes(id))resetListPage();
 render({preserve:false});
}
if(el('theme')){
 try{const saved=localStorage.getItem('arb-hub-theme');if(saved==='dark'||saved==='light')applyTheme(saved);else applyTheme('light');}catch(e){applyTheme('light');}
 el('theme').addEventListener('click',()=>applyTheme(document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark'));
}
['search','platform','source','period','status','owned','fee','notional','hours','sort'].forEach(id=>{const node=el(id);if(node)node.addEventListener('input',()=>onFilterChange(id));});
document.querySelectorAll('[data-page]').forEach(b=>b.addEventListener('click',()=>{page=b.dataset.page;render({preserve:false});}));
document.addEventListener('click',e=>{
 const expand=e.target.closest&&e.target.closest('button.expand');
 if(expand){const key=expand.getAttribute('data-key');if(expanded.has(key))expanded.delete(key);else expanded.add(key);render({preserve:true});return;}
 const th=e.target.closest&&e.target.closest('th.sortable');
 if(th&&th.dataset.col){
  const col=th.dataset.col,cur=sortMemory[page],pair={hourly:['hourly_desc','hourly_asc'],net:['net_desc','net_asc'],spread:['spread_desc','spread_asc'],basis:['basis_desc','basis_asc']}[col];
  if(!pair)return;
  sortMemory[page]=cur===pair[0]?pair[1]:pair[0];
  sortPage=null;syncSortOptions();resetListPage();render({preserve:true});return;
 }
 if(e.target.id==='page-prev'){listPageMemory[page]=Math.max(1,(listPageMemory[page]||1)-1);render({preserve:false});}
 if(e.target.id==='page-next'){listPageMemory[page]=(listPageMemory[page]||1)+1;render({preserve:false});}
});
setInterval(()=>{if(state)initialize(state,{preserve:true});},60000);
if(window.OFFLINE){initialize(JSON.parse(el('snapshot').textContent));}else if(el('stamp')){
 const poll=createPoller(()=>fetch('/api/state').then(r=>{if(!r.ok)throw Error('HTTP '+r.status);return r.json();}),()=>Date.now(),s=>initialize(s,{preserve:true}));
 const update=()=>{const p=poll.status();el('stamp').textContent='实时 · 下次读取缓存 '+p.countdown+' 秒 · 约30秒刷新'+(p.error?' · 陈旧 / 刷新失败：'+p.error:'');el('stamp').className=p.error?'stale':'';};
 poll.tick().then(update);setInterval(()=>{poll.tick().then(update);},1000);
}
function createPoller(read,clock,accept){
 let current=null,error=null,next=0,busy=false;
 return {status:()=>({state:current,error,countdown:Math.max(0,Math.ceil((next-clock())/1000))}),
 async tick(){if(busy||clock()<next)return;busy=true;next=clock()+30000;
 try{const value=await read();current=value;error=null;accept(value);}catch(e){error=String(e.message||e);}finally{busy=false;}}};
}
