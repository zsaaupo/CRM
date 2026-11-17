// Run with Node: node report/tests/download_ui.mjs
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const source = (await readFile(new URL('../static/report/js/download.js', import.meta.url), 'utf8'))
    .replace(/^export /gm, '');
class Element {
    constructor() {this.children=[]; this.dataset={}; this.attributes={}; this.disabled=false; this.hidden=false; this.textContent='';}
    append(...nodes) {this.children.push(...nodes);}
    setAttribute(name,value) {this.attributes[name]=value;}
    removeAttribute(name) {delete this.attributes[name];}
    focus() {this.focused=true;}
    click() {this.clicked=true;}
    remove() {this.removed=true;}
    querySelector(selector) {return selector === '[data-report-label]' ? this.label : this.children.find(node => 'reportDownload' in node.dataset) || null;}
}
async function setup({status=200, mime='application/pdf', delayed=false, empty=false}={}) {
    const trigger=new Element(); trigger.label=new Element(); trigger.label.textContent='Download PDF';
    const feedback=new Element(); const body=new Element(); const calls=[], revoked=[]; let release;
    const wait=delayed?new Promise(resolve=>{release=resolve;}):Promise.resolve();
    const context=vm.createContext({document:{querySelector:()=>null, addEventListener(){}, body,
        createElement:()=>new Element(),createElementNS:()=>new Element()},
        AbortController:class {constructor(){this.signal={};} abort(){}},
        window:{setTimeout:()=>1,clearTimeout(){}}, URL:{createObjectURL:()=> 'blob:report',revokeObjectURL:value=>revoked.push(value)},
        fetch:async(url,options)=>{calls.push({url,options}); await wait; return {ok:status===200,status,
            headers:{get:name=>name==='Content-Type'?mime:'attachment; filename="purchase-po-001.pdf"'},
            json:async()=>({detail:'Renderer unavailable'}),blob:async()=>({size:empty?0:100})};},
    });
    vm.runInContext(source,context); context.trigger=trigger; context.feedback=feedback;
    return {trigger,feedback,body,calls,release,context};
}
const delayed=await setup({delayed:true});
const pending=vm.runInContext("downloadReport('/report/api/purchases/7/pdf/',trigger,feedback)",delayed.context);
assert.equal(delayed.trigger.disabled,true); assert.equal(delayed.trigger.label.textContent,'Generating PDF...');
await vm.runInContext("downloadReport('/report/api/purchases/7/pdf/',trigger,feedback)",delayed.context);
assert.equal(delayed.calls.length,1); assert.equal(delayed.calls[0].options.credentials,'same-origin');
delayed.release(); await pending;
assert.equal(delayed.body.children[0].download,'purchase-po-001.pdf');
assert.equal(delayed.body.children[0].clicked,true); assert.equal(delayed.trigger.disabled,false);
assert.equal(delayed.trigger.label.textContent,'Download PDF');
for (const code of [403,404,503]) {
    const failed=await setup({status:code}); await vm.runInContext("downloadReport('/report/',trigger,feedback)",failed.context);
    assert.equal(failed.body.children.length,0); assert.equal(failed.feedback.hidden,false);
    assert.equal(failed.trigger.disabled,false); assert.equal(failed.feedback.dataset.tone,'error');
}
for (const options of [{mime:'application/json'},{empty:true}]) {
    const failed=await setup(options); await vm.runInContext("downloadReport('/report/',trigger,feedback)",failed.context);
    assert.equal(failed.body.children.length,0); assert.equal(failed.feedback.hidden,false);
}
const rows=new Element(); const row=new Element(); row.dataset.recordId='7';
const actions=new Element(); row.lastElementChild={firstElementChild:actions}; row.querySelector=()=>actions.children[0]||null;
rows.append(row); const panel={dataset:{reportUrlTemplate:'/report/api/transfers/0/pdf/'},querySelector:()=>rows};
delayed.context.testPanel=panel;
vm.runInContext('addReportActions(testPanel); addReportActions(testPanel);',delayed.context);
assert.equal(actions.children.length,1); assert.equal(actions.children[0].href,'/report/api/transfers/7/pdf/');
assert.equal(actions.children[0].title,'Download PDF');
console.log('Report UI passed: row actions, PDF blob download, filenames, permissions/errors, single click, loading and MIME checks.');
