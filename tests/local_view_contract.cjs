const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
(async()=>{
 const calls=[],elements=new Map(),intervals=[];let mountWrites=0,metricValue=10;
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
   const value=url.includes('/snapshot')?{target_key:url.split('/')[3],platform:{target:{key:url.split('/')[3]},live_charts:[],observation:{metrics:[{hostid:'1',itemid:'2',name:'Request duration',value:metricValue,status:'fresh',unit:'ms'}]}}}:structuredClone(state);
   return {ok:true,json:async()=>structuredClone(value)};
  }};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../lab/web-v2/app.local-view.js'),'utf8'),context);
 const flush=()=>new Promise(resolve=>setImmediate(resolve));await flush();await flush();
 assert.equal(intervals.length,1);assert.equal(intervals[0].ms,60000);
 assert.equal(elements.get('scene-list').children[0].children.at(-1).textContent,'发现与恢复已验证 · 分析待完善');
 assert.equal(elements.get('scene-clues-title').textContent,'平台实际发现与分析边界');
 assert.equal(elements.get('scene-clues').textContent,'Observed workflow; diagnosis remains limited.');
 const buttons=elements.get('scene-list').children,details=elements.get('all-metrics');
 await buttons[1].onclick();await flush();assert.equal(state.scenario,'query-efficiency');assert.equal(state.revision,0);assert(calls.every(c=>c.method==='GET'));
 assert.equal(elements.get('scene-list').children[1].children.at(-1).textContent,'负载已验证 · 平台验证中');
 assert.equal(elements.get('scene-clues-title').textContent,'待验证的联合线索');
 details.open=true;buttons[1].focus();const focused=document.activeElement;metricValue=30;
 await intervals[0].callback();await flush();await flush();
 assert.equal(mountWrites,1,'Auto refresh must never rebuild main');assert.equal(elements.get('all-metrics'),details);assert.equal(details.open,true);assert.equal(document.activeElement,focused);assert.equal(window.scrollY,620);assert(buttons[1].classes.has('selected'));
 const before=calls.length;await elements.get('refresh').onclick();await flush();assert(calls.slice(before).every(c=>c.method==='GET'));assert.equal(document.activeElement,focused);assert.equal(details.open,true);assert.equal(mountWrites,1);
 await elements.get('toggle').onclick();await flush();assert.equal(state.scenario,'stale-replica');assert.equal(state.desired_on,true);
 await buttons[0].onclick();await flush();assert.equal(state.scenario,'stale-replica');assert.equal(state.revision,1);
 await elements.get('toggle').onclick();await flush();
 assert.deepEqual(calls.filter(c=>c.method==='PUT').map(c=>[c.body.on,c.body.scenario]),[[true,'stale-replica'],[false,'stale-replica']]);
 console.log('60s partial refresh preserves DOM, scroll, selection, details and focus; browsing/manual refresh are GET-only.');
})().catch(error=>{console.error(error);process.exitCode=1;});
