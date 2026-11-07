/* Display helpers. No assignment, review or event mutations. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.InterfaceCore=api;})(globalThis,function(){
 'use strict';
 const defaults={spikes:'desc',isi:'asc',quality:'asc'};
 function compare(a,b,direction='asc'){
  const validA=Number.isFinite(a),validB=Number.isFinite(b);
  if(!validA||!validB)return validA?-1:validB?1:0;
  return (a-b)*(direction==='desc'?-1:1);
 }
 function toggle(sort,directions,key){return {...directions,[key]:sort===key?(directions[key]==='desc'?'asc':'desc'):directions[key]||defaults[key]};}
 function affected(event){
  const before=new Map((event.counts_before||[]).map(u=>[u.id,u.n])),after=new Map((event.counts_after||[]).map(u=>[u.id,u.n]));
  return [...new Set([event.task?.source,event.task?.target,...[...new Set([...before.keys(),...after.keys()])].filter(id=>before.get(id)!==after.get(id))])].filter(id=>Number.isSafeInteger(id)&&id>0);
 }
 function timeline(state,cluster,rows){
  // Walk retained lineage backwards; an undone merge never joins unrelated histories.
  const stack=[],known=new Map();for(const e of state.events||[]){if(e.type==='choice_applied'||e.type==='finished'){stack.push(e);known.set(e.id,e);}else if(e.type==='undo'){stack.pop();if(e.restored_operation_ids)stack.splice(0,stack.length,...e.restored_operation_ids.map(id=>known.get(id)).filter(Boolean));}}
  const ids=new Set([cluster]),ancestors=new Set();
  for(const e of [...stack].reverse()){
   const touched=affected(e);if(!touched.some(id=>ids.has(id)))continue;
   ancestors.add(e.id);
   if(e.choice?.action==='MERGE'){ids.delete(e.task.target);ids.add(e.task.source);ids.add(e.task.target);}
   if(e.choice?.action==='SPLIT'){
    const beforeIds=new Set((e.counts_before||[]).map(u=>u.id));
    for(const id of touched)if(!beforeIds.has(id))ids.delete(id);
    ids.add(e.task.source);
   }
  }
  // Only retained, confirmed actions belong in cluster history, including contributing ancestors.
  const retained=new Set(stack.map(e=>e.id??e));
  return rows.filter(r=>r.raw?.type==='choice_applied'&&retained.has(r.raw.id??r.raw)&&!['undone','superseded'].includes(r.status)&&(r.clusters.includes(cluster)||ancestors.has(r.raw.id))).map(r=>({...r,related:!r.clusters.includes(cluster)})).sort((a,b)=>(b.version||0)-(a.version||0));
 }
 function preferences(value={}){return {theme:['light','dark','system'].includes(value.theme)?value.theme:'light',density:['comfortable','compact'].includes(value.density)?value.density:'comfortable',showSession:!!value.showSession};}
 return {defaults,compare,toggle,affected,timeline,preferences};
});
