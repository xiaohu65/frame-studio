// Carry useful request context without putting query parameters or bodies in errors.
const stateRead = (method, path) => method === 'GET' && path === '/api/state';
const pathname = path => new URL(path, 'http://frame-studio.local').pathname;

function failure(message, {method,path,status=0,kind,code,serverMessage,current_revision,retryableRead=false}) {
  const diagnostic=`${method} ${path} · ${status ? `HTTP ${status}` : '无响应'} · ${code || kind}`;
  const error=new Error(`${message}${status>=500||!status||kind==='invalid-response'?`（${diagnostic}）`:''}`);
  Object.assign(error,{method,path,status,kind,code,serverMessage,current_revision,retryableRead,diagnostic});
  return error;
}

export async function request(path, options = {}) {
  const method=(options.method||'GET').toUpperCase(),route=pathname(path);
  const file=typeof File!=='undefined'&&options.body instanceof File;
  const context={method,path:route};
  const uncertain=method==='GET'?'请保留页面和草稿，重新读取状态。':'操作结果尚不能确认，未自动重发。请先核对项目或已有生成任务，勿重复提交。';
  let response;
  try {
    response=await fetch(path,{
      ...options,
      headers:file?{'Content-Type':'application/octet-stream',...(options.headers||{})}:{'Content-Type':'application/json',...(options.headers||{})},
      body:file?options.body:options.body===undefined?undefined:JSON.stringify(options.body),
    });
  } catch(cause) {
    if(cause.name==='AbortError'||options.signal?.aborted)throw cause;
    throw failure(`暂时连接不到本机服务。${uncertain}`,{...context,kind:'network',code:'SERVICE_UNREACHABLE',retryableRead:stateRead(method,route)});
  }
  const status=response.status,type=response.headers.get('content-type')||'';
  let text;
  try {text=await response.text();}
  catch(cause) {
    if(cause.name==='AbortError'||options.signal?.aborted)throw cause;
    throw failure(`本机服务响应未读取完整。${uncertain}`,{...context,status,kind:'network',code:'INCOMPLETE_RESPONSE',retryableRead:stateRead(method,route)});
  }
  let data;
  if(type.includes('json')) {
    try {data=JSON.parse(text);}
    catch {
      throw failure(`接口返回了无法读取的状态数据。${uncertain}`,{...context,status,kind:'invalid-response',code:'INVALID_JSON',retryableRead:stateRead(method,route)&&status>=500});
    }
  }
  if(data&&typeof data==='object'&&data.error) {
    const message=typeof data.error==='string'?data.error:data.error.message||'接口返回业务错误';
    throw failure(message,{...context,status,kind:'backend',serverMessage:message,code:data.code,current_revision:data.current_revision});
  }
  if(!response.ok) {
    const empty=!text.trim();
    const html=/<(?:!doctype|html|head|body|script)\b/i.test(text);
    const message=empty?`本机接口未返回错误详情（HTTP ${status}），可能正在重载，也可能是代理或服务异常。${uncertain}`:
      html?`接口返回了错误页面（HTTP ${status}）。${uncertain}`:
      data?`请求未完成（HTTP ${status}）。${uncertain}`:text.trim().slice(0,500);
    throw failure(message,{...context,status,kind:empty?'empty-response':'http',code:empty?'EMPTY_ERROR_RESPONSE':'HTTP_ERROR',
      retryableRead:stateRead(method,route)&&status>=500&&(empty||[502,503,504].includes(status))});
  }
  if(!data||typeof data!=='object') {
    throw failure(`接口没有返回项目状态数据，请核对当前页面入口。${uncertain}`,{...context,status,kind:'invalid-response',code:'EXPECTED_JSON'});
  }
  return data;
}
export const upload = (projectId, kind, file, extra={}) => request(`/api/upload?${new URLSearchParams({project_id:projectId,kind,filename:file.name,...extra})}`, {method:'POST',body:file});
export function assetUrl(asset) { return typeof asset==='string' ? asset : asset?.url || ''; }
export function time(seconds) { if(!Number.isFinite(Number(seconds))) return '—'; const n=Math.max(0,Number(seconds)); return `${String(Math.floor(n/60)).padStart(2,'0')}:${(n%60).toFixed(2).padStart(5,'0')}`; }
export const statusLabel = (s) => ({QUEUED:'排队中',RUNNING:'处理中',SUCCEEDED:'处理完成',FAILED:'处理失败',NEEDS_REVIEW:'待复核',UNKNOWN:'状态待确认',READY:'已就绪',DRAFT:'待准备',PENDING:'待准备',SUBMITTED:'已提交',PROCESSING:'生成中',COMPLETED:'已完成',GENERATED_NEEDS_REVIEW:'成片待复核',NEEDS_SETUP:'待配置'}[s] || s || '待准备');
