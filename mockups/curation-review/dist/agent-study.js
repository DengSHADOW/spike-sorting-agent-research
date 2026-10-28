/* Identity-free study helpers. The private model key never enters this file. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.AgentStudy=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const ID='anonymous-agents-v1';
  const SLOTS=['Agent_1','Agent_2','Agent_3'];
  const label=id=>SLOTS.includes(id)?id.replace('_',' '):'Historical agent';
  const available=(catalog,third=false)=>SLOTS.filter(id=>id==='Agent_3'?third:catalog.some(b=>b.study_id===ID&&b.study_agent_id===id));
  const visible=(catalog,agent,third=false)=>catalog.some(b=>b.study_id===ID)?catalog.filter(b=>b.source_kind==='initial'||b.study_id===ID&&b.study_agent_id===agent&&agent!=='Agent_3').sort((a,b)=>Number(!a.study_id)-Number(!b.study_id)):catalog;
  const choose=(catalog,agent,dataset)=>agent==='Agent_3'?null:catalog.find(b=>b.study_id===ID&&b.study_agent_id===agent&&b.dataset_id===dataset)||catalog.find(b=>b.study_id===ID&&b.study_agent_id===agent)||null;
  const matched=(cases,comparison)=>comparison?cases.find(c=>c.comparison_case_id===comparison):null;
  const queue=(catalog,agent,view='clusters')=>visible(catalog,agent).filter(b=>view==='history'?b.source_kind!=='initial':b.source_kind==='initial');
  function summary(reviews){
    return SLOTS.map(id=>{
      const rows=Object.values(reviews).map(v=>v.review||v).filter(r=>r&&r.study_id===ID&&r.study_agent_id===id);
      return {id,label:label(id),reviewed:rows.length,agreed:rows.filter(r=>r.expert_agree===true).length,disagreed:rows.filter(r=>r.expert_agree===false&&r.action!=='DEFER').length,deferred:rows.filter(r=>r.action==='DEFER').length};
    });
  }
  return {ID,SLOTS,label,available,visible,choose,matched,queue,summary};
});
