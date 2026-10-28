/* Action categories come from recorded actions, never waveform-text guesses. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory(require('./curation-reasons.js'));else root.ActionReasons=factory(root.CurationReasons);})(typeof globalThis!=='undefined'?globalThis:this,function(R){
 'use strict';
 const ACTIONS=['KEEP','SPLIT','MERGE','NOT_MERGE','DISCARD'],STORE='spikeagent-studio-action-reasons-v1';
 const key=text=>R.key(R.concise(text));
 const templates=[
  {text:'Consistent waveform shape and timing evidence support keeping this cluster.',actions:['KEEP']},
  {text:'No clear additional spike population to separate.',actions:['KEEP']},
  {text:'Different waveform shapes support keeping these clusters separate.',actions:['NOT_MERGE']},
  {text:'Timing or amplitude evidence does not support merging these clusters.',actions:['NOT_MERGE']}
 ];
 function rawAction(value){const parts=String(value||'').replace(/['"]/g,'').trim().split(/\s+/);if(parts.length===2&&/^s$/i.test(parts[0])&&/^\d+$/.test(parts[1]))return 'SPLIT';if(parts.length===3&&/^m$/i.test(parts[0])&&parts.slice(1).every(p=>/^\d+$/.test(p)))return Number(parts[1])===0?'DISCARD':'MERGE';return null;}
 function actionsFor(item,depth=0){
  if(!item||typeof item!=='object'||depth>8)return [];
  const found=new Set();
  for(const value of [item.action,item.expert_action,...(Array.isArray(item.actions)?item.actions:[])])if(ACTIONS.includes(value))found.add(value);
  const raw=rawAction(item.raw_action);if(raw)found.add(raw);
  for(const field of ['sources','references'])for(const child of Array.isArray(item[field])?item[field]:[])for(const action of actionsFor(child,depth+1))found.add(action);
  return [...found];
 }
 function catalog(rows,sources=[],links=[]){
  const tags=new Map(),add=(text,actions)=>{const k=key(text);if(!k)return;if(!tags.has(k))tags.set(k,new Set());for(const a of actions)if(ACTIONS.includes(a))tags.get(k).add(a);};
  for(const source of [...sources,...links])if(source&&typeof source.text==='string')add(source.text,actionsFor(source));
  const result=new Map();
  for(const row of rows){const k=key(row.text),actions=[...new Set([...actionsFor(row),...(tags.get(k)||[])])];if(actions.length)result.set(k,{...row,actions});}
  for(const row of [...templates,...links]){if(!row||!row.text)continue;const k=key(row.text),actions=actionsFor(row);if(!actions.length)continue;if(result.has(k)){result.get(k).actions=[...new Set([...result.get(k).actions,...actions])];continue;}result.set(k,{text:R.concise(row.text),kind:templates.includes(row)?'reviewer_template':'reviewer_added',original_texts:[],actions});}
  return [...result.values()].sort((a,b)=>a.text.localeCompare(b.text));
 }
 function forAction(rows,action){return ACTIONS.includes(action)?rows.filter(r=>r.actions.includes(action)):[];}
 function read(storage){try{const rows=JSON.parse(storage.getItem(STORE)||'[]');return Array.isArray(rows)?rows.filter(r=>r&&typeof r.text==='string'&&r.text.length<=2000&&actionsFor(r).length):[];}catch{return [];}}
 function remember(storage,text,action){
  if(!ACTIONS.includes(action)||typeof text!=='string'||!text.trim()||text.length>2000)return;
  const rows=read(storage),k=key(text),row=rows.find(r=>key(r.text)===k);
  if(row)row.actions=[...new Set([...actionsFor(row),action])];else rows.push({text:text.trim(),actions:[action]});
  storage.setItem(STORE,JSON.stringify(rows));
 }
 return {ACTIONS,STORE,key,rawAction,actionsFor,catalog,forAction,read,remember};
});
