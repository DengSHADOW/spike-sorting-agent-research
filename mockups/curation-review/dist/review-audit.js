/* Actual review-event interpretation; never generates workflow/learning revisions. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.ReviewAudit=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
  const FIELDS=['judgment_context','judgment_pair','expert_agree','action','expert_merge_target','disagreement_types','expert_reason','expert_note','expert_positive_comment','expert_confidence','evidence_sufficiency','missing_evidence','missing_evidence_note'];
  function entries(events,caseIds){
    const previous=new Map(),result=[];
    for(const event of events){
      if(!['review_saved','review_undone','review_imported'].includes(event.type)||!caseIds.has(event.case_id))continue;
      const review=event.type==='review_undone'?null:event.review||event;
      const before=previous.get(event.case_id)||null;
      const changes=FIELDS.filter(field=>JSON.stringify(before?.[field]??null)!==JSON.stringify(review?.[field]??null)).map(field=>({field,before:before?.[field]??null,after:review?.[field]??null}));
      result.push({case_id:event.case_id,type:event.type,revision:Number.isInteger(event.revision)?event.revision:null,timestamp:event.timestamp,agent_id:review?.study_agent_id||review?.agent_id||before?.study_agent_id||before?.agent_id||null,changes});
      previous.set(event.case_id,review);
    }
    return result.reverse();
  }
  return {entries};
});
