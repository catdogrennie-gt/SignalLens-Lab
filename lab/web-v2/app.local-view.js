const root=document.getElementById('root');
let state=null,viewKey=null,view=null,busy=false,error=false,refreshing=false,viewRequest=0,mounted=false;
let reportFingerprint=null,chartFingerprints=[],rowNodes=new Map(),sceneNodes=new Map();
const byId=id=>document.getElementById(id);
const setText=(id,value)=>{const n=typeof id==='string'?byId(id):id;const s=String(value??'');if(n.textContent!==s)n.textContent=s;};
const time=v=>v?new Date(v).toLocaleTimeString('zh-CN',{hour12:false}):'尚无实际记录';
const number=v=>typeof v==='number'&&Number.isFinite(v)?Number(v.toPrecision(4)).toLocaleString('zh-CN'):'未知';
const label=s=>({on:'负载已开启',off:'负载已关闭',stopping:'正在释放负载',unavailable:'暂不可用'}[s]??s);
// The legacy wire status certifies discovery/recovery workflow only. Diagnosis
// limits are shown separately; it must not imply confirmed root-cause accuracy.
const verification=s=>({platform_verified:'发现与恢复已验证 · 分析待完善',direct_verified:'负载已验证 · 平台验证中',implemented_awaiting_verification:'实现待验证',not_implemented:'尚未实现'}[s]??'验证中');
const claimStatus=s=>({observed:'已观测',supported_hypothesis:'证据支持的假设',insufficient_evidence:'证据不足',not_applicable:'不适用',refuted:'反证排除'}[s]??'待核实');
function mount(){
 if(mounted)return;mounted=true;
 root.innerHTML=`<span class="eyebrow">DB EVIDENCE · 场景验证</span><h1>监控有数据，平台能看出什么？</h1><p class="muted">查看真实业务场景，开启有界负载。平台通过 Zabbix 自行检查和分析；这里不会直接创建 AI 报告。</p>
 <div class="bar"><small id="refresh-status">每60秒自动刷新状态、指标和报告</small><button id="refresh" class="refresh">刷新数据</button></div>
 <div class="grid"><aside><div class="panel"><h2>场景目录</h2><p class="muted">目录只切换本页查看，不改变共享开关。</p><div id="scene-list"></div></div></aside><section>
 <div class="panel"><div class="bar"><div><h2 id="view-name"></h2><span class="status" id="view-status"></span></div><button class="switch" id="toggle">开启所选问题</button></div><p class="muted" id="global-status"></p><p class="muted" id="control-lease" hidden></p><p class="error" id="error" hidden>本页数据读取或操作未完成；不能据此判断数据库恢复。共享状态可能已被其他访客更新。</p></div>
 <div class="panel"><h2>这个场景验证什么</h2><h3>业务现象</h3><p id="scene-symptom"></p><h3>为什么要对照现有告警</h3><p id="scene-comparison"></p><h3 id="scene-clues-title">待验证的联合线索</h3><p id="scene-clues"></p><div id="scene-proof" hidden><h3>上次人工核对的实测 · 非当前实时值</h3><small id="scene-proof-meta"></small><p><a id="scene-report-link" target="_blank" rel="noopener noreferrer" hidden>查看对应完整报告 →</a></p><div class="table-scroll"><table><thead><tr><th>实测项</th><th>之前</th><th>问题时</th><th>关闭后</th></tr></thead><tbody>${[0,1,2,3,4,5].map(n=>`<tr id="proof-row-${n}" hidden><td id="proof-name-${n}"></td><td id="proof-before-${n}"></td><td id="proof-during-${n}"></td><td id="proof-after-${n}"></td></tr>`).join('')}</tbody></table></div></div></div>
 <div class="panel"><h2>平台正在做什么</h2><div class="facts"><div class="fact"><strong id="collection"></strong><small>实际平台取数周期</small></div><div class="fact"><strong id="signal"></strong><small>实际检查周期</small></div><div class="fact"><strong id="inspection"></strong><small>普通巡检状态</small></div></div><p class="muted" id="inspection-times"></p></div>
 <div class="panel" id="report-panel"><h2>发现与分析</h2><p class="empty" id="report-empty">暂未取得可展示的报告。没有报告不等于没有问题。</p><div id="report-content" hidden><p class="status" id="report-status"></p><h3 id="report-headline"></h3><p class="muted" id="report-window"></p>
 ${['phenomenon','surface0','surface1','surface2','joint','conclusion'].map(k=>`<div class="claim" id="claim-${k}"><h3><span id="claim-title-${k}"></span> <small id="claim-status-${k}"></small></h3><p id="claim-text-${k}"></p></div>`).join('')}
 <h3 id="actions-title">建议怎么做</h3><ul>${[0,1,2,3].map(n=>`<li id="action-${n}" hidden><span id="action-text-${n}"></span> <small id="action-type-${n}"></small></li>`).join('')}</ul><p class="muted">模型只提供建议，不会执行数据库变更。报告属于其证据时间窗；恢复请看之后的新采样。</p></div></div>
 <div class="panel"><h2>当前观测</h2><p class="muted" id="observation-status"></p><div class="metric-grid">${[0,1,2,3,4,5].map(n=>`<div class="metric" id="metric-${n}" hidden><small id="metric-name-${n}"></small><strong id="metric-value-${n}"></strong><small id="metric-time-${n}"></small></div>`).join('')}</div><details id="all-metrics"><summary id="metric-summary">查看全部观测</summary><div class="table-scroll"><table><thead><tr><th>资源 / 指标</th><th>值</th><th>采样</th></tr></thead><tbody id="metric-rows"></tbody></table></div></details></div>
 <div class="panel"><h2>数据趋势 · 实际采样</h2><p class="empty" id="charts-empty">等待实际数值曲线。</p>${[0,1,2,3,4,5].map(n=>`<div class="chart" id="chart-${n}" hidden><small id="chart-title-${n}"></small><canvas id="canvas-${n}" width="700" height="170"></canvas></div>`).join('')}</div>
 <p class="footer"><a id="platform-link" target="_blank" rel="noopener noreferrer">进入完整分析平台 →</a><br><span id="snapshot-time"></span> · 来自隔离测试业务，仅用于学习验证。</p></section></div>`;
 byId('toggle').onclick=()=>change(!state.desired_on,state.desired_on?state.scenario:viewKey);
 byId('refresh').onclick=()=>refresh(true);
}
function renderScenes(){
 for(const scene of state.scenarios){
  let nodes=sceneNodes.get(scene.key);
  if(!nodes){
   const button=document.createElement('button');button.className='scene';button.dataset.key=scene.key;
   nodes={button,title:document.createElement('strong'),symptom:document.createElement('small'),comparison:document.createElement('small'),clues:document.createElement('small'),status:document.createElement('small')};
   for(const name of ['title','symptom','comparison','clues','status'])button.appendChild(nodes[name]);
   button.onclick=()=>selectScenario(scene.key);byId('scene-list').appendChild(button);sceneNodes.set(scene.key,nodes);
  }
  nodes.button.classList.toggle('selected',scene.key===viewKey);nodes.button.disabled=!scene.available||busy;
  setText(nodes.title,scene.name);setText(nodes.symptom,'现象：'+(scene.business_symptom??scene.description));
  setText(nodes.comparison,'告警对照：'+(scene.zabbix_comparison??'待验证：需核对实际启用模板、阈值和事件。'));
  setText(nodes.clues,'联合线索：'+(scene.platform_clues??'待验证：核对同一业务对象的新鲜监控证据。'));
  setText(nodes.status,verification(scene.status)+(scene.available?'':' · 暂未开放'));
 }
}
function renderClaim(key,title,value){
 byId('claim-'+key).hidden=!value;if(!value)return;
 setText('claim-title-'+key,title);setText('claim-status-'+key,claimStatus(value.status));setText('claim-text-'+key,value.statement);
}
function renderReport(p,force){
 const r=p?.report,s=r?.executive_summary,fingerprint=JSON.stringify([r,p?.report_matches_current_observation]);
 if(fingerprint===reportFingerprint)return;
 const selection=document.getSelection?.();
 if(!force&&selection?.toString()&&byId('report-panel').contains(selection.anchorNode))return;
 reportFingerprint=fingerprint;byId('report-empty').hidden=!!s;byId('report-content').hidden=!s;if(!s)return;
 const historic=!p.report_matches_current_observation;
 setText('report-status',historic?'历史报告 · 尚未与当前观测关联':'已关联当前问题');byId('report-status').classList.toggle('warning',historic);
 setText('report-headline',s.headline);setText('report-window',`${r.mode==='live_redacted'?'模型分析':r.mode??'模式未知'} · 证据时间窗 ${time(r.from)} — ${time(r.to)}`);
 renderClaim('phenomenon','发生了什么',s.phenomenon);for(let n=0;n<3;n++)renderClaim('surface'+n,'直接原因 '+(n+1),s.surface_causes?.[n]);renderClaim('joint','联合原因',s.joint_root_cause);renderClaim('conclusion','问题结论',s.conclusion);
 byId('actions-title').hidden=!(s.recommended_actions?.length);for(let n=0;n<4;n++){const a=s.recommended_actions?.[n];byId('action-'+n).hidden=!a;if(a){setText('action-text-'+n,a.description);setText('action-type-'+n,a.kind==='manual_change'?'需人工确认后操作':'检查与观察');}}
}
function renderMetrics(o){
 const metrics=o?.metrics??[],score=m=>/Request duration|Database call duration|Pool acquisition|queue depth|Rows examined|temporary tables|transaction age|history list|version|freshness/i.test(m.name)?0:1;
 const top=[...metrics].sort((a,b)=>score(a)-score(b)).slice(0,6);
 setText('observation-status',`${({signals_present:'平台已发现变化信号',data_available:'已有数据，不能据此判定健康',unknown:'当前数据不足，状态未知'}[o?.status])??'等待平台观测'} · 最近采样 ${time(o?.observed_at)}`);
 for(let n=0;n<6;n++){const m=top[n];byId('metric-'+n).hidden=!m;if(m){setText('metric-name-'+n,`${m.host_name??''} · ${m.name}`);setText('metric-value-'+n,`${number(m.value)} ${m.unit??''}`);setText('metric-time-'+n,`${m.status==='fresh'?'有效采样':m.status==='stale'?'采样已过期':'状态未知'} · ${time(m.observed_at)}`);byId('metric-time-'+n).classList.toggle('warning',m.status!=='fresh');}}
 const visible=new Set();for(const m of metrics){const key=m.hostid+':'+m.itemid;visible.add(key);let row=rowNodes.get(key);if(!row){row=document.createElement('tr');for(let n=0;n<3;n++)row.appendChild(document.createElement('td'));byId('metric-rows').appendChild(row);rowNodes.set(key,row);}row.hidden=false;setText(row.children[0],`${m.host_name??''} · ${m.name}`);setText(row.children[1],`${number(m.value)} ${m.unit??''}`);setText(row.children[2],`${time(m.observed_at)} · ${m.status}`);}
 for(const [key,row] of rowNodes)if(!visible.has(key))row.hidden=true;
 setText('metric-summary',`查看全部 ${metrics.length} 项观测`);
}
function renderValidationProof(selected){
 const proof=selected?.validation_evidence;byId('scene-proof').hidden=!proof;if(!proof)return;
 const kinds={not_triggered_in_this_window:'本次时间窗未出现对应告警',already_alerted:'已有告警，补充关联解释',not_checked:'告警对照尚未完成'};
 setText('scene-proof-meta',`${kinds[proof.comparison_kind]??'尚未核对'} · 核对时间 ${proof.checked_at??'未记录'} · 报告 ${proof.report_id??'未记录'}`);
 byId('scene-report-link').hidden=!proof.report_url;if(proof.report_url)byId('scene-report-link').href=proof.report_url;
 for(let n=0;n<6;n++){const m=proof.measurements?.[n];byId('proof-row-'+n).hidden=!m;if(m){setText('proof-name-'+n,`${m.label} ${m.unit??''}`);for(const k of ['before','during','after'])setText('proof-'+k+'-'+n,number(m[k]));}}
}
function render(force=false){
 if(!state)return;mount();const scroll={x:window.scrollX,y:window.scrollY},focused=document.activeElement;
 const selected=state.scenarios.find(s=>s.key===viewKey),globalScene=state.scenarios.find(s=>s.key===state.scenario),p=view?.target_key===selected?.target_key?view.platform:null,i=p?.inspection,c=p?.pacing;
 renderScenes();setText('view-name',selected?.name);setText('view-status',state.scenario===viewKey?label(state.actual_state):'只读查看');
 setText('toggle',state.desired_on?'关闭正在运行的问题':'开启所选问题');byId('toggle').disabled=busy;byId('toggle').classList.toggle('on',state.desired_on);
 setText('global-status',`共享开关：${globalScene?.name??''} · ${label(state.actual_state)}。所有访客共用一个开关；目录只切换本页查看，按开关才改变真实负载。`);
 byId('control-lease').hidden=!state.control_lease_expires_at;setText('control-lease',`限时验收预计 ${time(state.control_lease_expires_at)} 自动停止；仍可随时关闭。`);byId('error').hidden=!error&&!state.error_code;
 setText('scene-symptom',selected?.business_symptom??selected?.description);setText('scene-comparison',selected?.zabbix_comparison??'待验证：以实际模板、阈值和事件对照为准。');setText('scene-clues-title',selected?.status==='platform_verified'?'平台实际发现与分析边界':'待验证的联合线索');setText('scene-clues',selected?.platform_clues??'尚未完成平台发现与解释验证。');
 setText('collection',`${c?.collection_seconds??'未知'} 秒`);setText('signal',`${c?.signal_seconds??'未知'} 秒`);setText('inspection',({paused:'巡检暂停',running:'正在分析',idle:'等待巡检',completed:'巡检完成',queued:'排队中'}[i?.status])??i?.status??'未连接');setText('inspection-times',`最近检查 ${time(i?.last_checked_at)} · 最近报告 ${time(i?.last_completed_at)}。AI按平台队列执行，不显示虚构进度。`);
 renderValidationProof(selected);renderReport(p,force);renderMetrics(p?.observation);const charts=p?.live_charts??[];byId('charts-empty').hidden=!!charts.length;
 for(let n=0;n<6;n++){const chart=charts[n];byId('chart-'+n).hidden=!chart;if(chart){setText('chart-title-'+n,`${chart.host_name??''} · ${chart.title??chart.id} · ${chart.unit??''}${chart.coverage?.complete===false?' · 采样不完整':''}`);const fp=JSON.stringify(chart);if(chartFingerprints[n]!==fp){chartFingerprints[n]=fp;draw(byId('canvas-'+n),chart);}}}
 byId('platform-link').hidden=!state.platform_url;if(state.platform_url)byId('platform-link').href=state.platform_url;setText('snapshot-time',`平台快照取得时间 ${time(view?.platform_received_at)}`);
 if(focused?.isConnected&&document.activeElement!==focused)focused.focus({preventScroll:true});if(window.scrollX!==scroll.x||window.scrollY!==scroll.y)window.scrollTo(scroll.x,scroll.y);
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
async function readView(force=false){
 const selected=state?.scenarios.find(s=>s.key===viewKey);if(!selected)return;
 const sequence=++viewRequest,key=viewKey;if(view?.target_key!==selected.target_key)view=null;
 try{const r=await fetch('/api/targets/'+encodeURIComponent(selected.target_key)+'/snapshot');if(!r.ok)throw Error();const received=await r.json();if(sequence===viewRequest&&key===viewKey){view=received;error=false;}}
 catch{if(sequence===viewRequest){view=null;error=true;}}
 if(sequence===viewRequest)render(force);
}
async function selectScenario(key){
 if(!state.scenarios.some(s=>s.key===key&&s.available))return;viewKey=key;view=null;reportFingerprint=null;chartFingerprints=[];render(true);await readView(true);
}
async function refresh(manual=false){
 if(refreshing)return;refreshing=true;
 try{const r=await fetch('/api/state');if(!r.ok)throw Error();state=await r.json();if(!viewKey)viewKey=state.scenario;error=false;await readView(manual);setText('refresh-status',`每60秒自动刷新状态、指标和报告 · 最近读取 ${new Date().toLocaleTimeString('zh-CN',{hour12:false})}`);}
 catch{error=true;render();}finally{refreshing=false;}
}
async function change(on,scenario){
 if(busy)return;busy=true;render();
 try{const r=await fetch('/api/switch',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({on,scenario,expected_revision:state.revision,expected_generation:state.generation_id})});if(!r.ok)throw Error();state=await r.json();error=false;render();await readView(true);}
 catch{error=true;}finally{busy=false;render();}
}
refresh();setInterval(()=>refresh(false),60000);
