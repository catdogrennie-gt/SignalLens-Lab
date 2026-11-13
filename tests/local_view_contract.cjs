const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
(async()=>{
 const calls=[],elements=new Map(),intervals=[];let mountWrites=0,metricValue=10,ruleFindings=[];
 const window={scrollX:0,scrollY:620,scrollTo(x,y){this.scrollX=x;this.scrollY=y;}};
 const document={activeElement:null,getElementById:id=>elements.get(id),getSelection:()=>null,createElement:tag=>new Element(tag)};
 class Element{
  constructor(tag='div',id=null){this.tagName=tag;this.id=id;this.children=[];this.dataset={};this.hidden=false;this.open=false;this.isConnected=true;this._text='';this.classes=new Set();this.classList={toggle:(key,on)=>on?this.classes.add(key):this.classes.delete(key)};if(id)elements.set(id,this);}
  set innerHTML(html){mountWrites++;this.html=html;for(const m of html.matchAll(/id="([^"]+)"/g))new Element('div',m[1]);if(mountWrites>1)document.activeElement=null;}
  get innerHTML(){return this.html;}
  set textContent(s){if(this.id?.startsWith('metric-value-')&&this._text!==s)window.scrollY+=5;this._text=s;}
  get textContent(){return this._text;}
  appendChild(node){node.parentNode=this;this.children.push(node);return node;}
  contains(node){return node===this||this.children.some(n=>n.contains(node));}
  focus(){document.activeElement=this;}
 }
 const root=new Element('main','root');
 const scenes=[{key:'query-efficiency',target_key:'svc-a',name:'SQL',available:true,status:'platform_verified',platform_clues:'Observed workflow; diagnosis remains limited.'},{key:'stale-replica',target_key:'svc-c',name:'Replica',available:true,status:'direct_verified'}];
 const state={revision:0,generation_id:'g'.repeat(32),scenario:'query-efficiency',desired_on:false,actual_state:'off',scenarios:scenes};
 const context={document,window,console,setInterval:(callback,ms)=>{intervals.push({callback,ms});return 1;},
  fetch:async(url,options={})=>{
   calls.push({url,method:options.method??'GET',body:options.body?JSON.parse(options.body):null});
   if(url==='/api/switch'){const b=JSON.parse(options.body);state.scenario=b.scenario;state.desired_on=b.on;state.actual_state=b.on?'on':'off';state.revision++;}
   const value=url.includes('/snapshot')?{target_key:url.split('/')[3],platform:{target:{key:url.split('/')[3]},live_charts:[],rule_findings:ruleFindings,observation:{metrics:[{hostid:'1',itemid:'2',name:'Request duration',value:metricValue,status:'fresh',unit:'ms'}]}}}:structuredClone(state);
   return {ok:true,json:async()=>structuredClone(value)};
  }};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../lab/web-v2/app.local-view.js'),'utf8'),context);
 const flush=()=>new Promise(resolve=>setImmediate(resolve));await flush();await flush();
 assert.equal(intervals.length,0,'Automatic refresh starts OFF');
 assert.equal(elements.get('scene-list').children[0].children.at(-1).textContent,'发现与恢复已验证 · 分析待完善');
 assert.equal(elements.get('scene-clues-title').textContent,'平台实际发现与分析边界');
 assert.equal(elements.get('scene-clues').textContent,'Observed workflow; diagnosis remains limited.');
 const buttons=elements.get('scene-list').children,details=elements.get('all-metrics');
 await buttons[1].onclick();await flush();assert.equal(state.scenario,'query-efficiency');assert.equal(state.revision,0);assert(calls.every(c=>c.method==='GET'));
 assert.equal(elements.get('view-status').textContent,'此场景未运行');
 assert.equal(elements.get('toggle').textContent,'开启此场景：Replica');
 assert.equal(elements.get('toggle').disabled,false,'An available OFF scene is actionable, not read-only.');
 assert.equal(elements.get('toggle').classes.has('on'),false);
 assert.equal(elements.get('scene-list').children[1].children.at(-1).textContent,'负载已验证 · 平台验证中');
 assert.equal(elements.get('scene-clues-title').textContent,'待验证的联合线索');
 details.open=true;buttons[1].focus();const focused=document.activeElement;metricValue=30;
 await elements.get('refresh').onclick();await flush();await flush();
 assert.equal(mountWrites,1,'Manual reads must not rebuild main');assert.equal(elements.get('all-metrics'),details);assert.equal(details.open,true);assert.equal(document.activeElement,focused);assert.equal(window.scrollY,620);assert(buttons[1].classes.has('selected'));
 ruleFindings=[{title:'连接容量余量不足',summary:'占用85%，剩余6，关注水位80%',status:'active',observed_at:'2026-11-12T15:26:13Z'}];
 await elements.get('refresh').onclick();await flush();
 assert.equal(elements.get('observed-rules').hidden,false);
 assert.equal(elements.get('rule-title-0').textContent,'连接容量余量不足');
 assert.equal(elements.get('rule-summary-0').textContent,'占用85%，剩余6，关注水位80%');
 ruleFindings=[];await elements.get('refresh').onclick();await flush();
 assert.equal(elements.get('observed-rules').hidden,true,'No active rule must not fabricate one');
 const before=calls.length;await elements.get('refresh').onclick();await flush();assert(calls.slice(before).every(c=>c.method==='GET'));assert.equal(document.activeElement,focused);assert.equal(details.open,true);assert.equal(mountWrites,1);
 await elements.get('toggle').onclick();await flush();assert.equal(state.scenario,'stale-replica');assert.equal(state.desired_on,true);
 assert.equal(elements.get('view-status').textContent,'此场景正在运行');
 assert.equal(elements.get('toggle').classes.has('on'),true);
 assert.equal(elements.get('toggle').textContent,'关闭正在运行的：Replica');
 await buttons[0].onclick();await flush();assert.equal(state.scenario,'stale-replica');assert.equal(state.revision,1);
 assert.equal(elements.get('view-status').textContent,'此场景未运行');
 assert.equal(elements.get('toggle').classes.has('on'),false,'Viewing another scene must not inherit the running scene green state.');
 assert.equal(elements.get('toggle').textContent,'关闭正在运行的：Replica','The global OFF action must name the real running scene.');
 assert.match(elements.get('global-status').textContent,/当前运行：Replica/);
 assert.match(elements.get('global-status').textContent,/一次只运行一个场景/);
 await elements.get('toggle').onclick();await flush();
 assert.equal(elements.get('view-status').textContent,'此场景未运行');
 assert.equal(elements.get('toggle').textContent,'开启此场景：SQL');
 assert.equal(elements.get('toggle').disabled,false);
 assert.equal(elements.get('toggle').classes.has('on'),false);
 assert.deepEqual(calls.filter(c=>c.method==='PUT').map(c=>[c.body.on,c.body.scenario]),[[true,'stale-replica'],[false,'stale-replica']]);
 await buttons[1].onclick();await flush();state.desired_on=true;state.actual_state='off';
 await elements.get('refresh').onclick();await flush();await flush();
 assert.equal(elements.get('toggle').classes.has('on'),false,'Requested ON is not an actual running confirmation.');
 state.actual_state='unavailable';await elements.get('refresh').onclick();await flush();await flush();
 assert.equal(elements.get('view-status').textContent,'暂不可用','Unavailable actual state must not be presented as confirmed OFF.');
 assert.match(elements.get('global-status').textContent,/状态未知：Replica/);
 state.desired_on=false;state.actual_state='unexpected';await elements.get('refresh').onclick();await flush();await flush();
 assert.equal(elements.get('view-status').textContent,'运行状态待确认');
 assert.match(elements.get('global-status').textContent,/运行状态待确认：Replica/);
 assert.doesNotMatch(elements.get('global-status').textContent,/当前没有运行/);
 assert.equal(elements.get('toggle').classes.has('on'),false);
 state.desired_on=true;state.actual_state='starting';await elements.get('refresh').onclick();await flush();await flush();
 assert.equal(elements.get('view-status').textContent,'运行状态待确认');
 assert.match(elements.get('global-status').textContent,/运行状态待确认：Replica/);
 assert.equal(elements.get('toggle').classes.has('on'),false);
 state.actual_state='off';await elements.get('refresh').onclick();await flush();await flush();
 assert.match(elements.get('global-status').textContent,/正在开启：Replica/);
 assert.doesNotMatch(elements.get('global-status').textContent,/当前没有运行/);
 state.desired_on=false;await elements.get('refresh').onclick();await flush();await flush();
 assert.match(elements.get('global-status').textContent,/当前没有运行中的场景/);
 state.scenarios[1].status='analysis_verified';await elements.get('refresh').onclick();await flush();await flush();
 assert.equal(buttons[1].children.at(-1).textContent,'本场景发现、解释与恢复已验证');
 state.scenarios[1].available=false;await elements.get('refresh').onclick();await flush();await flush();
 assert.equal(elements.get('toggle').disabled,true,'Revoked scene cannot be restarted.');
 state.desired_on=true;await elements.get('refresh').onclick();await flush();await flush();
 assert.equal(elements.get('toggle').disabled,false,'Revoked running scene remains stoppable.');
 console.log('Default automatic refresh is OFF; explicit reads and observed rule cards remain GET-only.');
})().catch(error=>{console.error(error);process.exitCode=1;});
