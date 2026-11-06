import {mergeProjectPatch} from './draftMerge.js';

export async function saveWithRecovery({project,partial,draft,read,write,onLatest,choose}){
 let base=project,body=partial,context=draft;
 for(let attempt=0;attempt<4;attempt++){
  try{return await write(body);}
  catch(error){
   if(error.status!==409||error.code!=='REVISION_CONFLICT')throw error;
   const response=await read(),latest=response.project||response;
   onLatest(latest);
   let merged=mergeProjectPatch(base,body,latest,context);
   if(merged.conflicts.length){
    const choices=await choose(merged.conflicts);
    if(!choices)return null;
    merged=mergeProjectPatch(base,body,latest,context,choices);
    if(merged.conflicts.length)throw new Error('请为每项冲突选择保留内容；草稿仍保留');
   }
   base=latest;body={...merged.patch,expected_revision:latest.revision};context=null;
  }
 }
 throw new Error('项目仍在持续更新，草稿已保留，请稍候再保存');
}
