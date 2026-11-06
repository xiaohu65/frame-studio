import {copy,mergeDraft} from './draftMerge.js';

export const DRAFTS=new Map();
let version=0,entryId=0;
const listeners=new Set();
export function draftChanged(){version++;for(const listener of listeners)listener();if(typeof window!=='undefined')window.dispatchEvent(new Event('frame-studio-drafts'));}
export const draftSnapshot=()=>version;
export function subscribeDraft(listener){listeners.add(listener);return()=>listeners.delete(listener);}
export function changeDraft(identity,value,revision,next){
 const prior=DRAFTS.get(identity);
 const result=typeof next==='function'?next(prior?.value??value):next;
 const entry={...prior,value:copy(result),base:copy(prior?.base??value),revision:prior?.revision??revision,entryId:prior?.entryId??++entryId,editVersion:(prior?.editVersion||0)+1};
 DRAFTS.set(identity,entry);draftChanged();return entry;
}
export function finishDraftSave(identity,submitted,savedValue,revision){
 const current=DRAFTS.get(identity);
 // A discarded/recreated draft or newer save must survive a late response.
 if(!current||current.entryId!==submitted.entryId||current.saving!==submitted.saving)return;
 if(current.editVersion===submitted.editVersion)DRAFTS.delete(identity);
 else{
  let merged=mergeDraft(submitted.value,current.value,savedValue);
  // Typing after submission remains an unsaved edit, while untouched fields
  // adopt the actual saved response (including the user's conflict choices).
  if(merged.conflicts.length)merged=mergeDraft(submitted.value,current.value,savedValue,Object.fromEntries(merged.conflicts.map(conflict=>[conflict.key,'mine'])));
  DRAFTS.set(identity,{...current,base:copy(savedValue),value:merged.value,revision,saving:null});
 }
 draftChanged();
}
