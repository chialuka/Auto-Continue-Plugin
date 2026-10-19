const fs=require('node:fs'),vm=require('node:vm'),cp=require('node:child_process'),readline=require('node:readline');
const [python,fixture,plugin,mode]=process.argv.slice(2);
const child=cp.spawn(python,[fixture,'--backend',mode==='cold-panel-no-events'?'no-events':'events'],{stdio:['pipe','pipe','pipe']});
let sequence=0;const pending=new Map();let stderr='';child.stderr.on('data',b=>stderr+=b);
readline.createInterface({input:child.stdout}).on('line',line=>{const m=JSON.parse(line),p=pending.get(m.id);if(!p)return;pending.delete(m.id);m.error?p.reject(Error(m.error)):p.resolve(m.result)});
child.on('exit',code=>{for(const p of pending.values())p.reject(Error(stderr||`backend exited ${code}`));pending.clear()});
function call(method,params){return new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});child.stdin.write(JSON.stringify({id,method,params})+'\n')})}
const thread='12345678-1234-4234-8234-123456789abc';
const instruction="Resume the task that was stopped when the usage limit was reached in this chat. Continue from where it left off, preserving the user's instructions and ordinary permissions. If no work remains, report that briefly.";
const modelCalls=[],appCalls=[];
async function modelTool(name,args){const result=await call('tools/call',{name,arguments:args});modelCalls.push({name,arguments:args,result});return result}
class Element{constructor(){this.textContent='';this.children=[];this.dataset={};this.style={setProperty(){}};this.value='';this.disabled=false}append(...xs){this.children.push(...xs)}replaceChildren(){this.children=[]}setAttribute(k,v){this[k]=v}}
async function main(){
 let buttonEnabled=null,uiError=null;
 if(mode==='direct'||mode==='direct-default'){
  await modelTool('resume_direct_enable',mode==='direct-default'?{thread,timezone:'Africa/Lagos'}:{thread,instruction,timezone:'Africa/Lagos'});
 }else{
  let opening=null;
  if(mode!=='warm-panel')opening=await modelTool('resume_meter',{thread});
  const elements=new Map(['#meter','#time','#error','#connection','#connection-label','#notice','#direct-instruction','#direct-state','#direct-enable','#direct-cancel'].map(x=>[x,new Element()]));
  const listeners=[];let parent;const inflight=new Set();
  function receive(data){for(const f of listeners)f({source:parent,data:{jsonrpc:'2.0',...data}})}
  parent={postMessage(m){
   if(!m.id)return;
   if(m.method==='ui/initialize'){queueMicrotask(()=>receive({id:m.id,result:{}}));return}
   if(m.method==='tools/call'){
    appCalls.push(m.params.name);
    const p=call(m.method,m.params).then(result=>receive({id:m.id,result}),error=>receive({id:m.id,error:{message:error.message}}));
    inflight.add(p);p.finally(()=>inflight.delete(p));
   }
  }};
  const context={document:{querySelector:k=>elements.get(k),createElement:()=>new Element(),addEventListener(){},visibilityState:'visible'},parent,addEventListener:(type,fn)=>{if(type==='message')listeners.push(fn)},setInterval(){},setTimeout(){return 1},clearTimeout(){},console};
  vm.createContext(context);
  const html=fs.readFileSync(plugin+'/web/meter.html','utf8');
  vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1],context);
  if(mode!=='unbound-panel')receive({method:'ui/notifications/tool-input',params:{arguments:{thread}}});
  if(opening)receive({method:'ui/notifications/tool-result',params:opening});
  await new Promise(setImmediate);
  await Promise.all([...inflight]);
  buttonEnabled=!elements.get('#direct-enable').disabled;
  elements.get('#direct-instruction').value=instruction;
  await vm.runInContext('enableDirect()',context);
  await Promise.all([...inflight]);
  if(buttonEnabled && elements.get('#direct-instruction').value!==instruction)throw Error('Enable response changed the saved-message field');
  uiError=elements.get('#error').textContent;
 }
 const saved=await call('fixture/state',{});
 console.log(JSON.stringify({case:mode,agentTurnsRequired:mode==='warm-panel'?0:1,modelMediatedToolCalls:modelCalls.length,appToolCalls:appCalls,buttonEnabled,uiError,armed:saved.job?.status==='armed',due:saved.job?.due??null,savedInstruction:saved.job?.instruction??null,stats:saved.stats,modelCalls}));
 child.stdin.end();
}
main().catch(error=>{console.error(error.stack,stderr);child.kill();process.exitCode=1});
