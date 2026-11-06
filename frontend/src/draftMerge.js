// Merge only edits made since the draft's own base. Arrays with stable domain
// keys preserve concurrent additions and treat delete-versus-edit as a conflict.
export const copy=value=>value===undefined?undefined:JSON.parse(JSON.stringify(value));
const equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const labels={source_actors:'人物',observations:'出现位置',roles:'角色',segments:'片段',label:'名称',source_description:'辨认依据',target_role_id:'替换角色',scope:'替换方式',time:'原片时间',visibility:'可见状态',confirmed:'人物位置确认',box:'定位框',color:'标识颜色',images:'参考图',name:'名称',description:'说明',mask_keyframes:'轮廓关键帧'};
function keyed(rows){
 if(!rows.every(object))return null;
 const keys=rows.map(row=>row.id!=null?`id:${row.id}`:row.segment_id!=null&&Number.isFinite(row.time)?`位置 ${row.segment_id} · ${row.time} 秒`:Number.isFinite(row.time)?`时间 ${row.time} 秒`:null);
 return keys.every(key=>key!==null)&&new Set(keys).size===keys.length?keys:null;
}
export function mergeDraft(base,mine,latest,choices={}){
 const conflicts=[];
 function merge(b,m,r,path){
  if(equal(m,b))return copy(r);
  if(equal(r,b)||equal(m,r))return copy(m);
  if(object(b)&&object(m)&&object(r)){
   const result={};
   for(const key of new Set([...Object.keys(r),...Object.keys(m),...Object.keys(b)])){
    const value=merge(b[key],m[key],r[key],[...path,key]);
    if(value!==undefined)result[key]=value;
   }
   return result;
  }
  if(Array.isArray(b)&&Array.isArray(m)&&Array.isArray(r)){
   const bk=keyed(b),mk=keyed(m),rk=keyed(r);
   if(bk&&mk&&rk){
    const bm=new Map(bk.map((key,i)=>[key,b[i]])),mm=new Map(mk.map((key,i)=>[key,m[i]])),rm=new Map(rk.map((key,i)=>[key,r[i]])),result=[];
    for(const key of new Set([...rk,...mk,...bk])){
     const value=merge(bm.get(key),mm.get(key),rm.get(key),[...path,key]);
     if(value!==undefined)result.push(value);
    }
    return result;
   }
  }
  const key=JSON.stringify(path),choice=choices[key];
  if(choice==='mine')return copy(m);
  if(choice==='latest')return copy(r);
  conflicts.push({key,path,label:path.map(part=>labels[part]||part.replace(/^id:/,'')).join(' / '),base:copy(b),mine:copy(m),latest:copy(r)});
  return copy(r);
 }
 return {value:merge(base,mine,latest,[]),conflicts};
}

export function mergeProjectPatch(baseProject,partial,latestProject,draft,choices={}){
 const patch={},conflicts=[];
 for(const field of Object.keys(partial).filter(key=>key!=='expected_revision')){
  let base=baseProject[field],mine=partial[field],latest=latestProject[field],wrap=value=>value;
  if(draft&&['source_actors','roles'].includes(field)&&draft.base?.id){
   const id=draft.base.id;
   base=draft.base;mine=mine.find(row=>row.id===id);latest=latest?.find(row=>row.id===id);
   wrap=value=>{
    const rows=copy(latestProject[field]||[]),index=rows.findIndex(row=>row.id===id);
    if(index>=0){if(value===undefined)rows.splice(index,1);else rows[index]=value;}
    else if(value!==undefined)rows.push(value);
    return rows;
   };
  }else if(draft&&field==='segments'&&draft.identity.includes('mask')){
   const segment=partial.segments.find(row=>draft.identity.endsWith('mask'+row.id));
   if(!segment)throw new Error('草稿所属片段已变化，请返回原片段核对');
   base=draft.base;mine=segment.mask_keyframes;latest=latest?.find(row=>row.id===segment.id)?.mask_keyframes;
   if(!latestProject.segments?.some(row=>row.id===segment.id))throw new Error('草稿所属片段已删除；未覆盖最新项目');
   wrap=value=>latestProject.segments.map(row=>row.id===segment.id?{...row,mask_keyframes:value}:copy(row));
  }else if(draft&&Object.keys(partial).filter(key=>key!=='expected_revision').length===1){base=draft.base;}
  const result=mergeDraft({[field]:base},{[field]:mine},{[field]:latest},choices);
  patch[field]=wrap(result.value[field]);conflicts.push(...result.conflicts);
 }
 return {patch,conflicts};
}

export function newerProject(current,next){
 return current&&next&&current.id===next.id&&Number(current.revision)>Number(next.revision)?current:next;
}
