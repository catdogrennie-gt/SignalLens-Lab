const root = document.getElementById('root');
let state = null, busy = false, error = false;
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time = v => v ? new Date(v).toLocaleTimeString('zh-CN', {hour12:false}) : '尚无实际记录';
const number = v => typeof v === 'number' ? Number(v.toPrecision(4)).toLocaleString('zh-CN') : '未知';
const label = s => ({on:'负载已开启',off:'负载已关闭',stopping:'正在释放负载',unavailable:'暂不可用'}[s] ?? s);
const verification = s => ({platform_verified:'平台闭环已验证',direct_verified:'负载已验证 · 平台验证中',implemented_awaiting_verification:'实现待验证',not_implemented:'尚未实现'}[s] ?? '验证中');
const claimStatus = s => ({observed:'已观测',supported_hypothesis:'证据支持的假设',insufficient_evidence:'证据不足',not_applicable:'不适用',refuted:'反证排除'}[s] ?? '待核实');
function claim(title, value) {
  if (!value) return '';
  return `<div class="claim"><h3>${esc(title)} <small>${esc(claimStatus(value.status))}</small></h3><p>${esc(value.statement)}</p></div>`;
}
function reportPanel(p) {
  const r = p?.report, s = r?.executive_summary;
  if (!s) return '<p class="empty">暂未取得可展示的报告。未生成报告不等于没有问题，也不表示故障已恢复。</p>';
  const historical = !p.report_matches_current_observation;
  return `<p class="status ${historical?'warning':''}">${historical?'历史报告 · 尚未与当前观测关联':'已关联当前问题'}</p>
    <h3>${esc(s.headline)}</h3><p class="muted">${r.mode==='live_redacted'?'实时模型分析':esc(r.mode)} · 证据时间窗 ${time(r.from)} — ${time(r.to)}</p>
    ${claim('发生了什么',s.phenomenon)}
    ${(s.surface_causes??[]).map((v,n)=>claim(`直接原因 ${n+1}`,v)).join('')}
    ${claim('联合原因',s.joint_root_cause)}${claim('问题结论',s.conclusion)}
    ${(s.recommended_actions??[]).length?`<h3>建议怎么做</h3><ul>${s.recommended_actions.map(a=>`<li>${esc(a.description)} <small>${a.kind==='manual_change'?'需人工确认后操作':'检查与观察'}</small></li>`).join('')}</ul>`:''}
    <p class="muted">分析只提供建议，不会自动执行数据库变更。报告证据属于上述时间窗，当前恢复请看新采样。</p>`;
}
function metricsPanel(observation) {
  const metrics=observation?.metrics??[];
  const prioritized=[...metrics].sort((a,b)=> {
    const score=v=>/Request duration|Database call duration|Pool acquisition|queue depth|Rows examined|temporary tables|transaction age|history list|version|freshness/i.test(v.name)?0:1;
    return score(a)-score(b);
  });
  return `<p class="muted">${({signals_present:'平台已发现变化信号',data_available:'已有监控数据，尚不能据此判定健康',unknown:'当前数据不足，状态未知'}[observation?.status]??'等待平台观测')} · 最近采样 ${time(observation?.observed_at)}</p>
    ${metrics.length?`<div class="metric-grid">${prioritized.slice(0,6).map(m=>`<div class="metric"><small>${esc(m.host_name)} · ${esc(m.name)}</small><strong>${number(m.value)} <small>${esc(m.unit)}</small></strong><small class="${m.status==='fresh'?'':'warning'}">${m.status==='fresh'?'有效采样':m.status==='stale'?'采样已过期':'采样状态未知'} · ${time(m.observed_at)}</small></div>`).join('')}</div>
    <details><summary>查看全部 ${metrics.length} 项观测</summary><div class="table-scroll"><table><thead><tr><th>资源 / 指标</th><th>值</th><th>采样</th></tr></thead><tbody>${metrics.map(m=>`<tr><td><small>${esc(m.host_name)}</small><br>${esc(m.name)}</td><td>${number(m.value)} ${esc(m.unit)}</td><td>${time(m.observed_at)}<br><small>${esc(m.status)}</small></td></tr>`).join('')}</tbody></table></div></details>`:'<p class="empty">尚无实际数值；不会用零或模拟值替代。</p>'}`;
}
function render() {
  if (!state) return;
  const detailsOpen=root.querySelector('details')?.open??false;
  const selected=state.scenarios.find(s=>s.key===state.scenario), p=state.platform, i=p?.inspection, c=p?.pacing, charts=p?.live_charts??[];
  root.innerHTML=`<span class="eyebrow">DB EVIDENCE · 场景验证</span><h1>监控有数据，平台能看出什么？</h1>
  <p class="muted">选择真实业务场景，开启固定的有界负载。平台通过 Zabbix 自行检查和分析；这里不会直接创建 AI 报告。</p>
  <div class="grid"><aside><div class="panel"><h2>场景目录</h2>${state.scenarios.map(s=>`<button class="scene ${s.key===state.scenario?'selected':''}" data-key="${esc(s.key)}" ${!s.available||busy?'disabled':''}><strong>${esc(s.name)}</strong><small>${esc(s.description)}</small><small>${esc(verification(s.status))}${s.available?'':' · 暂未开放'}</small></button>`).join('')}</div></aside><section>
  <div class="panel"><div class="bar"><div><h2>${esc(selected?.name)}</h2><span class="status">${esc(label(state.actual_state))}</span></div><button class="switch ${state.desired_on?'on':''}" id="toggle" ${busy?'disabled':''}>${state.desired_on?'关闭问题':'开启问题'}</button></div>
  <p class="muted">所有访客共享一个开关；重复开启不会叠加。切换场景先停止旧负载。控制服务失联后，负载租约自动失效。</p>
  ${state.error_code?'<p class="error">负载或分析平台连接暂不可用，未续期的负载会自动停止。</p>':''}${error?'<p class="error">本页连接中断，正在重试；不能据此判断数据库恢复。</p>':''}</div>
  <div class="panel"><h2>平台正在做什么</h2><div class="facts"><div class="fact"><strong>${esc(c?.collection_seconds??'未知')} 秒</strong><small>实际平台取数周期</small></div><div class="fact"><strong>${esc(c?.signal_seconds??'未知')} 秒</strong><small>实际检查周期</small></div><div class="fact"><strong>${esc(({paused:'巡检暂停',running:'正在分析',idle:'等待巡检',completed:'巡检完成',queued:'排队中'}[i?.status])??i?.status??'未连接')}</strong><small>普通巡检状态</small></div></div>
  <p class="muted">最近检查 ${time(i?.last_checked_at)} · 最近报告 ${time(i?.last_completed_at)}<br>AI 分析按平台队列执行，时间来自实际状态，没有固定进度动画。</p></div>
  <div class="panel"><h2>发现与分析</h2>${reportPanel(p)}</div>
  <div class="panel"><h2>当前观测</h2>${metricsPanel(p?.observation)}</div>
  <div class="panel"><h2>数据趋势 · 实际采样</h2>${charts.length?charts.slice(0,6).map((x,n)=>`<div class="chart"><small>${esc(x.host_name)} · ${esc(x.title??x.id)} · ${esc(x.unit??'')} ${x.coverage?.complete===false?' · 采样不完整':''}</small><canvas data-chart="${n}" width="700" height="170" aria-label="${esc(x.title??'监控趋势')}"></canvas></div>`).join(''):'<p class="empty">等待所选对象的实际数值曲线。</p>'}</div>
  <p class="footer">${state.platform_url?`<a href="${esc(state.platform_url)}" target="_blank" rel="noopener noreferrer">进入完整分析平台 →</a>`:''}<br>平台快照取得时间 ${time(state.platform_received_at)} · 来自隔离测试业务，仅用于学习验证。</p></section></div>`;
  if(root.querySelector('details'))root.querySelector('details').open=detailsOpen;
  root.querySelector('#toggle').onclick=()=>change(!state.desired_on,state.scenario);
  root.querySelectorAll('[data-key]').forEach(b=>b.onclick=()=>change(state.desired_on,b.dataset.key));
  charts.slice(0,6).forEach((chart,n)=>draw(root.querySelector(`[data-chart="${n}"]`),chart));
}
function draw(canvas,chart) {
  if (!canvas) return;
  const ctx=canvas.getContext('2d'), pts=chart.points??[], valid=pts.filter(p=>typeof p.value==='number'&&Number.isFinite(p.value));
  ctx.font='11px sans-serif';ctx.fillStyle='#69808d';
  if (!valid.length) {ctx.fillText('没有可绘制的实际数据',15,75);return;}
  const lo=Math.min(...valid.map(p=>p.value)), hi=Math.max(...valid.map(p=>p.value)), span=hi-lo||1;
  const t0=Date.parse(pts[0].time), t1=Date.parse(pts.at(-1).time), dt=t1-t0||1;
  const gaps=pts.slice(1).map((p,n)=>Date.parse(p.time)-Date.parse(pts[n].time)).filter(v=>v>0).sort((a,b)=>a-b), cadence=gaps[Math.floor(gaps.length/2)]??Infinity;
  ctx.strokeStyle='#e2e9ef';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(55,15);ctx.lineTo(55,140);ctx.lineTo(690,140);ctx.stroke();
  ctx.fillText(number(hi),2,23);ctx.fillText(number(lo),2,140);ctx.fillText(time(pts[0].time),55,162);ctx.fillText(time(pts.at(-1).time),635,162);
  ctx.strokeStyle='#0b8997';ctx.lineWidth=2;ctx.beginPath();let previous=null;
  pts.forEach(p=>{if(typeof p.value!=='number'||!Number.isFinite(p.value)){previous=null;return;}const stamp=Date.parse(p.time),x=55+(stamp-t0)/dt*635,y=135-(p.value-lo)/span*110;if(previous!==null&&stamp-previous<=cadence*3)ctx.lineTo(x,y);else ctx.moveTo(x,y);previous=stamp;});ctx.stroke();
  const last=valid.at(-1);ctx.fillStyle='#0b8997';ctx.fillText(`最近 ${number(last.value)} ${chart.unit??''}`,470,15);
}
async function read() {try{const r=await fetch('/api/state');if(!r.ok)throw Error();state=await r.json();error=false;render();}catch{error=true;render();}setTimeout(read,2000);}
async function change(on,scenario) {if(busy)return;busy=true;render();try{const r=await fetch('/api/switch',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({on,scenario})});if(!r.ok)throw Error();state=await r.json();error=false;}catch{error=true;}finally{busy=false;render();}}
read();
