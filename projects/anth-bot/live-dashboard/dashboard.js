
const $=id=>document.getElementById(id);
const sg=(x,d=1)=>x==null||!isFinite(x)?"—":(x>0?"+":"")+Number(x).toFixed(d);
const cls=x=>x>0?"pos":x<0?"neg":"";
const f2=x=>x==null||!isFinite(x)?"—":Number(x).toFixed(2);
const mid=o=>o&&isFinite(o.bid)&&isFinite(o.ask)?(o.bid+o.ask)/2:null;
const bj=t=>new Date(t).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false});
const dur=ms=>{if(ms==null||!isFinite(ms))return "—";const m=Math.max(0,Math.round(ms/60000));return m<60?m+"分钟":Math.floor(m/60)+"小时"+(m%60?m%60+"分":"")};
const DIR={L:"买io / 卖Lighter",S:"卖io / 买Lighter"};
let chart, lastOk=0;

function modeText(d){
  if(d.mode==="live") return d.dryRun?"实盘逻辑（不发单）":d.liveEnabled&&d.venuesReady&&!d.stale?"实盘运行中":"实盘模式 · 暂停执行";
  if(d.mode==="paper") return "模拟";
  if(d.mode==="stopped") return "已停止";
  return d.mode||"—";
}
function draw(d){
  const ageMs=d&&Number.isFinite(d.ts)?Date.now()-d.ts:Infinity;
  const stale = !d || d.ok===false || ageMs>90000 || ageMs < -10000;
  if(stale){
    $("banner").style.background="#3d1214"; $("banner").style.border="1px solid #f85149";
    $("banner").textContent=d&&d.ok!==false?"⚠️ 数据已过期或时间无效，当前运行状态未知；请勿据旧快照判断停止或运行。最后快照："+(Number.isFinite(d.ts)?bj(d.ts):"未知"):"⚠️ 看板连接中断，当前运行状态未知："+(d&&d.err?d.err:"请稍后刷新");
    $("mode").textContent="状态未知";$("mode").className="v yel";$("modeS").textContent="无法确认当前服务状态，旧数据仅供历史参考";$("dIo").className="dot off";$("dLt").className="dot off";$("statusHistory").textContent="";$("alert").textContent="";
    return;
  }
  lastOk=Date.now();
  const live=d.mode==="live"&&!d.dryRun&&d.liveEnabled&&d.venuesReady&&!d.stale;
  $("banner").style.border="1px solid "+(live?"#8e1519":d.mode==="paper"?"#1b4332":"#30363d");
  $("banner").style.background=live?"#3d1214":d.mode==="paper"?"#0f2a1a":"#21262d";
  const age=d.ts?Math.max(0,Math.round((Date.now()-d.ts)/1000)):null;
  $("banner").textContent=(live?"🔴 实盘运行中：允许按策略使用真实资金；当前是否成交以订单记录为准":d.mode==="paper"?"🟢 模拟盘：只记账，不下单":d.dryRun?"🟡 实盘逻辑（DRY_RUN，不发真实订单）":d.mode==="live"?"⚠️ 实盘模式 · 暂停执行：检查行情、客户端或实盘开关":"⏸ 已停止：不开新仓")
    +" ｜ 信号："+(d.signal||"—")+" ｜ 快照 "+bj(d.ts)+"（"+age+"秒前；服务器约30秒推送）";
  const alerts=[];
  const legacy="双边持仓已核实归零；执行安全修复已部署，策略保持停止，待人工确认";
  const history=live&&!d.recoveryExposure&&!d.exit_pending&&d.alert===legacy;
  $("statusHistory").textContent=d.statusNotice|| (history?"历史提示已解除：此前安全修复后等待确认；当前状态以实时运行栏为准。":"");
  if(d.alert&&!history) alerts.push(d.alert);
  if(d.mode==="stopped") alerts.push("机器人已停止");
  if(d.stale) alerts.push("行情数据中断");
  if(d.keys&&(d.keys.io==="FAIL"||d.keys.lt==="FAIL")) alerts.push("密钥自检失败（"+["io="+d.keys.io,"Lighter="+d.keys.lt].join("，")+"）");
  $("alert").innerHTML=alerts.length?alerts.map(a=>`<div class="alert">${a}</div>`).join(""):"";

  $("mode").textContent=modeText(d);
  $("mode").className="v "+(live?"neg":d.mode==="paper"?"pos":"");
  const kIo=d.keys&&d.keys.io, kLt=d.keys&&d.keys.lt;
  $("modeS").textContent=`客户端 ${d.venuesReady?"就绪":"未连接"}${d.venuesErr?"（"+d.venuesErr+"）":""} ｜ 密钥 io ${kIo||"—"} / Lighter ${kLt||"—"} ｜ 杠杆 ${d.cfg&&d.cfg.lev?d.cfg.lev+"x":"—"} ｜ 每边 ${d.cfg?d.cfg.margin+"U":"—"}${d.cfg&&d.cfg.confirmSec!=null?` ｜ 信号持续 ${d.cfg.confirmSec}s · 最短持仓 ${d.cfg.minHoldSec}s · 最低净利 ${d.cfg.minProfitBp}bp`:""}`;

  const io=(d.prices||{}).io||{}, lt=(d.prices||{}).lt||{};
  const mIo=mid(io), mLt=mid(lt);
  $("pIo").textContent=mIo?mIo.toFixed(1):"—"; $("baIo").textContent=`买一 ${io.bid??"—"} / 卖一 ${io.ask??"—"}`;
  $("pLt").textContent=mLt?mLt.toFixed(1):"—"; $("baLt").textContent=`买一 ${lt.bid??"—"} / 卖一 ${lt.ask??"—"}`;
  $("dIo").className="dot "+(d.stale?"off":"on"); $("dLt").className="dot "+(d.stale?"off":"on");

  const openBp=(d.cfg&&d.cfg.openBp)||25;
  $("spr").textContent=`${sg(d.spread)} / ${sg(d.median)} bp`;
  const xd=Math.max(d.xDevS??-1e9,d.xDevL??-1e9), hasX=isFinite(xd)&&xd>-1e8;
  const cs=(d.cfg&&d.cfg.confirmSec)||2;
  const remain=hasX?openBp-xd:null;
  let devTxt=`中间价偏离 ${sg(d.dev)}bp ｜ 按可成交价偏离 ${hasX?sg(xd):"—"}bp（阈值 ${openBp}bp，需连续 ${cs}s）`;
  if(d.position) devTxt+=" ｜ 已持仓";
  else if(d.confirm) devTxt+=` ｜ 信号确认中 ${Number(d.confirm.sec).toFixed(1)}/${cs}s`;
  else if(remain!=null) devTxt+= remain>0?` ｜ 再偏 ${remain.toFixed(1)}bp 才会开仓`:" ｜ 已达到开仓偏离";
  if(d.stale) devTxt+=" ⚠️数据中断";
  $("dev").textContent=devTxt;
  $("trigBar").hidden=d.dev==null;
  if(d.dev!=null){const pct=Math.max(0,Math.min(100,(hasX?Math.max(0,xd):Math.abs(d.dev))/openBp*100));$("trigFill").style.width=pct+"%";$("trigFill").style.background=pct>=100?"var(--r)":"var(--b)";}

  const p=d.position;
  if(p){
    $("uPnL").textContent=sg(p.uPnL,2)+"U"; $("uPnL").className="v "+cls(p.uPnL);
    $("posS").innerHTML=`${p.dirLabel||DIR[p.dir]||p.dir}（${p.live?"实盘":"模拟"}）已持 ${dur(p.heldMs)}<br>实际入场 ${sg(p.entrySpread??p.spread)}bp（信号 ${sg(p.sigSpread)}bp，实际偏离 ${sg(p.entryDev)}bp${p.weak?" ⚠️偏小，只在有利润时平":""}）→ 目标 ${sg(p.target)}bp ｜ 当前可成交出场 ${sg(p.exitSpr)}bp<br>数量 io ${p.qIo} / Lighter ${p.qLt} ｜ 实际成交价 io ${f2(p.pxIo)} / Lighter ${f2(p.pxLt)} ｜ 扣费后浮动 ${sg(p.bp)}bp<br>${p.holdNote?p.holdNote+"<br>":""}强平价 io ${f2(p.liqIo)}（距 ${p.liqDistIo==null?"—":p.liqDistIo.toFixed(1)}%）/ Lighter ${f2(p.liqLt)}（距 ${p.liqDistLt==null?"—":p.liqDistLt.toFixed(1)}%）`;
  }else{
    $("uPnL").textContent="无持仓"; $("uPnL").className="v"; $("posS").textContent="空仓观望";
  }

  const T=d.totals||{};
  $("net").textContent=sg(T.net,2)+"U"; $("net").className="v "+cls(T.net);
  const wr=T.winRate==null?"—":(T.winRate*100).toFixed(0)+"%";
  $("totS").textContent=`${T.count||0} 笔 ｜ ${T.wins||0}胜 ${T.losses||0}负 ｜ 胜率 ${wr}`;

  const b=d.balances||{}, bio=b.io||{}, blt=b.lt||{};
  $("balIo").textContent=(bio.usdc==null?"—":f2(bio.usdc)+" U");
  $("balIoS").textContent=`${bio.mode==="unifiedAccount"?"统一账户":(bio.mode||"—")} ｜ 冻结 ${f2(bio.hold)} ｜ ANTH 持仓 ${bio.pos??"—"} ｜ 强平价 ${f2(bio.liq)}`;
  $("balLt").textContent=(blt.collateral==null?"—":f2(blt.collateral)+" U");
  $("balLtS").textContent=`可用 ${f2(blt.avail)} ｜ ANTH 持仓 ${blt.pos??"—"} ｜ 强平价 ${f2(blt.liq)}`;

  const rows=d.trades||[];
  let h="";
  rows.forEach((r,i)=>{h+=`<tr><td>${i+1}</td><td>${bj(r.t)}</td><td>${DIR[r.dir]||r.dir}</td><td>${dur(r.hold)}</td><td>${sg(r.inS)}bp</td><td>${sg(r.outS)}bp</td><td class="${cls(r.bp)}">${sg(r.bp)}bp</td><td class="${cls(r.u)}">${r.u==null?"—":sg(r.u,4)}${r.real||!r.live?"":" (估算)"}</td><td>${r.uBal==null?"—":sg(r.uBal,4)}</td><td>${r.fees==null?"—":Number(r.fees).toFixed(4)}</td><td class="s">${r.uEst==null?"—":sg(r.uEst,4)+" 估算"}</td><td style="text-align:left">${r.reason||"—"}${r.note?'<div class="s" style="white-space:normal;max-width:260px">'+r.note+'</div>':""}</td><td>${r.live?"实盘":"模拟"}</td></tr>`});
  $("tb").innerHTML=h||'<tr><td colspan="13" class="s">暂无成交</td></tr>';

  const ol=[...(d.olog||[])].reverse();
  $("ol").innerHTML=ol.map(e=>`<tr><td>${bj(e.t)}</td><td class="${e.kind==="err"?"neg":e.kind==="fill"?"pos":""}">${e.kind||""}</td><td style="white-space:normal;text-align:left">${e.msg||""}</td></tr>`).join("")||'<tr><td colspan="3" class="s">暂无</td></tr>';

  const S=d.series||[], W=((d.cfg&&d.cfg.medMin)||120)*60000, M=[];
  for(let i=0;i<S.length;i++){const a=S.filter(x=>x[0]>S[i][0]-W&&x[0]<=S[i][0]).map(x=>x[1]).sort((x,y)=>x-y);if(a.length)M.push({x:S[i][0],y:a[Math.floor(a.length/2)]})}
  const ds=chart.data.datasets;ds[0].data=S.map(x=>({x:x[0],y:x[1]}));ds[1].data=M;
  ds[2].data=M.map(m=>({x:m.x,y:m.y+openBp}));ds[3].data=M.map(m=>({x:m.x,y:m.y-openBp}));
  chart.update("none");
  $("foot").textContent=`数据由东京服务器上的机器人定时推送到这块看板，页面本身不能改模式、不能下单。时间均为北京时间（UTC+8，与香港相同）。上次成功刷新：${bj(Date.now())}。`;
}

chart=new Chart($("ch"),{type:"line",data:{datasets:[
  {label:"价差",data:[],borderColor:"#58a6ff",pointRadius:0,borderWidth:1.2},
  {label:"中位数",data:[],borderColor:"#d29922",pointRadius:0,borderWidth:1.2},
  {label:"上轨",data:[],borderColor:"rgba(248,81,73,.5)",borderDash:[4,4],pointRadius:0,borderWidth:1},
  {label:"下轨",data:[],borderColor:"rgba(63,185,80,.5)",borderDash:[4,4],pointRadius:0,borderWidth:1}
]},options:{animation:false,maintainAspectRatio:false,parsing:false,
  scales:{x:{type:"linear",ticks:{color:"#8b949e",callback:v=>new Date(v).toLocaleTimeString("zh-CN",{timeZone:"Asia/Shanghai",hour:"2-digit",minute:"2-digit",hour12:false})}},
          y:{ticks:{color:"#8b949e",callback:v=>v+" bp"}}},
  plugins:{legend:{labels:{color:"#c9d1d9"}}}}});

async function loop(){
  try{
    const r=await fetch("api/status",{cache:"no-store",signal:AbortSignal.timeout(8000)});
    if(!r.ok)throw Error("状态接口暂不可用");
    const d=await r.json();
    draw(d);
  }catch(e){
    draw({ok:false,err:"网络连接或状态读取失败"});
  }
}
setInterval(()=>{if(!document.hidden)loop();},10000);loop();
document.addEventListener("visibilitychange",()=>{if(!document.hidden)loop();});
