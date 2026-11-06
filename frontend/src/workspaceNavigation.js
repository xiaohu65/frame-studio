const MODES = new Set(['original','puppet','compare','generated','segment-source']);
const STEP_NAMES = {source:0,people:1,scene:2,review:3,export:4};
export function parseWorkspaceQuery(search='') {
  const q=new URLSearchParams(search), raw=q.get('step');
  const number=raw===null?undefined:Number.isNaN(Number(raw))?STEP_NAMES[raw]:Number(raw);
  return {projectId:/^[A-Za-z0-9_-]{1,64}$/.test(q.get('project')||'')?q.get('project'):null,
    step:Number.isInteger(number)&&number>=0&&number<=4?number:undefined,
    segmentId:/^[A-Za-z0-9_-]{1,64}$/.test(q.get('segment')||'')?q.get('segment'):undefined,
    mode:MODES.has(q.get('view'))?q.get('view'):undefined};
}
export function readSavedWorkspace(storage) {
  try { const value=JSON.parse(storage?.getItem('frame-studio-workspace-v2')||'{}');return value&&typeof value==='object'?value:{}; } catch {return {};}
}
export function initialWorkspace(search,storage) {
  const query=parseWorkspaceQuery(search), saved=readSavedWorkspace(storage);
  return {...saved,query,projectId:query.projectId||saved.projectId||null};
}
export function workspaceForProject(project,search='',storage) {
  const query=parseWorkspaceQuery(search), saved=readSavedWorkspace(storage).projects?.[project.id]||{};
  const preferred=query.projectId===project.id?{...saved,...Object.fromEntries(Object.entries(query).filter(([,v])=>v!==undefined&&v!==null))}:saved;
  const segments=project.segments||[];
  return {step:Number.isInteger(preferred.step)&&preferred.step>=0&&preferred.step<=4?preferred.step:0,
    segmentId:segments.some(s=>s.id===preferred.segmentId)?preferred.segmentId:segments[0]?.id||'',
    mode:MODES.has(preferred.mode)?preferred.mode:project.workflow==='replacement'?'puppet':'original'};
}
export function saveWorkspace(storage,projectId,value) {
  try {const current=readSavedWorkspace(storage);const next={projectId,projects:{...(current.projects||{}),[projectId]:value}};storage?.setItem('frame-studio-workspace-v2',JSON.stringify(next));}catch{/* Browser privacy settings must not prevent editing. */}
}
export function workspaceSearch(projectId,{step,segmentId,mode}) {
  const q=new URLSearchParams({project:projectId,step:String(step)});
  if(segmentId)q.set('segment',segmentId);if(MODES.has(mode))q.set('view',mode);
  return '?'+q.toString();
}
