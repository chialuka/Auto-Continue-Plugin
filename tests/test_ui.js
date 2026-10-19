const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../web/meter.html'),'utf8');
class Element{constructor(tag){this.tag=tag;this.textContent='';this.children=[];this.dataset={};this.style={values:{},setProperty(k,v){this.values[k]=v}}}append(...nodes){this.children.push(...nodes)}replaceChildren(){this.children=[]}setAttribute(k,v){this[k]=v}}
const elements=new Map(['#meter','#time','#error','#connection','#connection-label','#notice','#direct-instruction','#direct-state','#direct-enable','#direct-cancel'].map(k=>[k,new Element()]));let intervalMs;
const context={document:{querySelector:k=>elements.get(k),createElement:tag=>new Element(tag),addEventListener:()=>{},visibilityState:'visible'},parent:{postMessage:()=>{}},addEventListener:()=>{},setInterval:(fn,ms)=>intervalMs=ms,setTimeout:()=>1,clearTimeout:()=>{},console};
vm.createContext(context);vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1],context);
function render(used,age=5){context.data={thread:'a',automatic:true,observedAt:Date.now()/1000-age,meter:{windows:[{window:'primary',usedPercent:used,windowDurationMins:300,resetsAt:1792363397},{window:'secondary',usedPercent:null}]}};vm.runInContext('render(data)',context)}
render(20);let card=elements.get('#meter').children[0];assert.equal(card.dataset.health,'normal');assert.equal(card.children[2].children[0].textContent,'80%');assert.equal(card.children[1].children[0]['aria-valuenow'],'80');assert.equal(card.children[1].children[0].children[0].children[0].style.values['--remaining'],'80%');assert.equal(intervalMs,5000);
render(80);assert.equal(elements.get('#meter').children[0].dataset.health,'low');render(96);assert.equal(elements.get('#meter').children[0].dataset.health,'critical');render(100);assert.equal(elements.get('#meter').children[0].children[2].children[0].textContent,'0%');
render(20,1000);assert.equal(elements.get('#connection-label').textContent,'Waiting for activity');assert.equal(elements.get('#meter').children[1].children[2].children[0].textContent,'—');
vm.runInContext('render({meter:null})',context);assert.match(elements.get('#meter').children[0].children[0].textContent,/Connect the meter once/);
console.log('Battery UI passed: remaining-fill accuracy, health thresholds, unknown/empty state, stale status and five-second automatic refresh.');

assert.equal(elements.get('#direct-enable').disabled,true,'unbound meter must not arm a different chat');
context.directData={thread:'a',directJob:{status:'armed',instruction:'Frozen instruction',due:1792381476}};
vm.runInContext("boundThread='a';renderDirect(directData)",context);
assert.equal(elements.get('#direct-enable').disabled,true);
assert.equal(elements.get('#direct-cancel').disabled,false);
assert.equal(elements.get('#direct-instruction').value,'Frozen instruction');
context.directData={thread:'a',directJob:{status:'armed',due:1792381476}};
vm.runInContext('renderDirect(directData)',context);
assert.equal(elements.get('#direct-instruction').value,'Frozen instruction','compact enable confirmation must preserve the edited instruction');
vm.runInContext("boundThread=null;renderDirect(directData)",context);
assert.equal(elements.get('#direct-enable').disabled,true);
console.log('Direct UI passed: current-chat binding, immutable active instruction and cancellation controls.');
