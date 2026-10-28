/* Browser-local reviewer identities; display names never act as authentication. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.ReviewerProfiles=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
  const KEY='spikeagent-reviewer-profiles-v1';
  function list(storage){
    let values=[];try{values=JSON.parse(storage.getItem(KEY)||'[]');}catch{}
    if(!Array.isArray(values))return [];
    return [...new Map(values.filter(r=>r&&/^[A-Za-z0-9_-]{8,100}$/.test(r.id)&&typeof r.name==='string'&&r.name.length<=100).map(r=>[r.id,{id:r.id,name:r.name}])).values()];
  }
  function remember(storage,id,name){
    if(!/^[A-Za-z0-9_-]{8,100}$/.test(id)||typeof name!=='string'||name.length>100)throw new Error('Invalid reviewer profile.');
    const values=list(storage),entry=values.find(r=>r.id===id);
    if(entry)entry.name=name.trim();else values.push({id,name:name.trim()});
    storage.setItem(KEY,JSON.stringify(values));return values;
  }
  const initials=name=>Array.from(name.trim()).filter(c=>!/[\s]/.test(c)).slice(0,2).join('').toUpperCase()||'ID';
  return {list,remember,initials};
});
