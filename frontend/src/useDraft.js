import {useSyncExternalStore} from 'react';
import {copy} from './draftMerge.js';
import {DRAFTS,changeDraft,draftChanged,draftSnapshot,finishDraftSave,subscribeDraft} from './draftState.js';

export default function useDraft(value,identity,revision){
 useSyncExternalStore(subscribeDraft,draftSnapshot,draftSnapshot);
 const existing=DRAFTS.get(identity),draft=existing?.value??value;
 const change=next=>changeDraft(identity,value,revision,next);
 const save=async(onPatch,partial)=>{
  const entry=DRAFTS.get(identity);
  if(!entry||entry.saving)return null;
  const submitted={...entry,saving:Symbol('save')};
  DRAFTS.set(identity,submitted);draftChanged();
  try{
   const result=await onPatch({...partial,expected_revision:entry.revision},{identity,base:copy(entry.base),value:copy(entry.value)});
   if(result){
    const p=result.project||result;
    const maskSegment=partial.segments?.find(s=>identity.endsWith('mask'+s.id));
    const saved=maskSegment?p.segments?.find(s=>s.id===maskSegment.id)?.mask_keyframes:partial.segments?p.segments:partial.source_actors?p.source_actors?.find(a=>a.id===value.id):partial.roles?p.roles?.find(r=>r.id===value.id):partial.scene?p.scene:entry.value;
    finishDraftSave(identity,submitted,saved??entry.value,p.revision);
   }
   return result;
  }finally{
   const current=DRAFTS.get(identity);
   if(current?.saving===submitted.saving){DRAFTS.set(identity,{...current,saving:null});draftChanged();}
  }
 };
 const reset=()=>{DRAFTS.delete(identity);draftChanged();};
 return [draft,change,!!existing,save,reset];
}
