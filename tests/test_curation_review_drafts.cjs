const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const Core=require('../mockups/curation-review/dist/review-core.js');
const app=fs.readFileSync(require('node:path').join(__dirname,'../mockups/curation-review/dist/app.js'),'utf8');
const source=app.slice(app.indexOf("const DRAFT_PREFIX="),app.indexOf('function selectCase('));
function make(){
 const c={id:'case-a',dataset_id:'recording',state_sha256:'state-one',run_id:'run',agent_id:'Agent_1',source:'U-1',units:[{id:'U-1',cluster_id:1},{id:'U-2',cluster_id:2}]};
 const storage=new Map(),fields={'#reason-select':{value:'Reason'},'#review-note':{value:'Work in progress'},'#positive-comment':{value:''},'#storage-warning':{hidden:true}};
 const context={Core,Map,reviewerId:'reviewer-a',mode:'real',activeBundle:'bundle-a',CASES:[c],state:{reviews:{}},revisions:{},isDirty:true,storageAvailable:true,
 draft:{agree:false,action:'SPLIT',target:'',reason:'',note:'',positive:'',confidence:'high',sufficiency:'sufficient',missing:[],missing_note:'',disagreement_types:['action']},
 ACTIONS:{KEEP:{},SPLIT:{},DISCARD:{},MERGE:{},DEFER:{}},comparisonChoices:new Map(),comparisonTarget:()=>'',currentCase:()=>c,$:id=>fields[id],
 localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)}};
 vm.createContext(context);vm.runInContext(source+'\nglobalThis.helpers={cacheLocalDraft,restoreLocalDraft,clearLocalDraft,lastSelectedCase,selectionKey};',context);return {c,context,storage,fields,...context.helpers};
}
test('an unfinished review restores its actual editor values without becoming a saved judgment',()=>{
 const a=make();a.cacheLocalDraft();a.context.draft={};a.context.isDirty=false;assert.equal(a.restoreLocalDraft(a.c),true);assert.equal(a.context.draft.note,'Work in progress');assert.equal(a.context.draft.reason,'Reason');assert.equal(a.context.isDirty,true);assert.equal(Object.keys(a.context.state.reviews).length,0);
});
test('draft recovery rejects a different checkpoint, agent or newer saved revision',()=>{
 for(const field of ['state_sha256','agent_id','run_id']){const a=make();a.cacheLocalDraft();assert.equal(a.restoreLocalDraft({...a.c,[field]:'different'}),false);}
 const a=make();a.cacheLocalDraft();a.context.revisions[a.c.id]=2;assert.equal(a.restoreLocalDraft(a.c),false);
});
test('drafts and selected cases are reviewer-scoped, and discard removes the draft',()=>{
 const a=make();a.cacheLocalDraft();a.context.reviewerId='reviewer-b';assert.equal(a.restoreLocalDraft(a.c),false);a.context.reviewerId='reviewer-a';a.clearLocalDraft(a.c.id);assert.equal(a.restoreLocalDraft(a.c),false);
 a.storage.set(a.selectionKey(),a.c.id);assert.equal(a.lastSelectedCase(),a.c.id);a.storage.set(a.selectionKey(),'unavailable');assert.equal(a.lastSelectedCase(),null);
});
test('storage failure displays a warning and never creates a saved review',()=>{
 const a=make();a.context.localStorage.setItem=()=>{throw Error('Quota')};a.cacheLocalDraft();assert.equal(a.context.storageAvailable,false);assert.equal(a.fields['#storage-warning'].hidden,false);assert.equal(Object.keys(a.context.state.reviews).length,0);
});
