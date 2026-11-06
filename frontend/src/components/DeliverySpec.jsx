import {Alert,AlertDescription} from "./ui/alert";
import React from 'react';


export function aspectRatio(width,height){
 if(!Number.isInteger(width)||!Number.isInteger(height)||width<=0||height<=0)return '待检查';
 let a=width,b=height;
 while(b)[a,b]=[b,a%b];
 return `${width/a}:${height/a}`;
}

export function specSeconds(value){return Number.isFinite(value)?`${value.toFixed(6)} 秒`:'待检查';}

export function DeliverySpecFacts({spec={},className='preservation-list'}){
 const {model,resolution,ratio,request_ratio,width,height,fps,model_output_fps,frame_count,duration_seconds}=spec||{};
 return <dl className={className}>
  {model&&<div><dt>生成模型</dt><dd>{model}</dd></div>}
  <div><dt>档位</dt><dd>{resolution||'未选择'}</dd></div>
  <div><dt>画幅</dt><dd>{ratio||'待检查'}{request_ratio==='adaptive'?' · 请求自适应':request_ratio&&request_ratio!==ratio?` · 请求 ${request_ratio}`:''}</dd></div>
  <div><dt>目标宽高</dt><dd>{width&&height?`${width} × ${height}`:'待核实'}</dd></div>
  <div><dt>交付帧率</dt><dd>{fps?`${fps} 帧/秒`:'待检查'}</dd></div>
  {model_output_fps&&model_output_fps!==fps&&<div><dt>模型输出帧率</dt><dd>{model_output_fps} 帧/秒</dd></div>}
  <div><dt>完整帧数</dt><dd>{Number.isInteger(frame_count)?`${frame_count} 帧`:'待检查'}</dd></div>
  <div><dt>画面覆盖</dt><dd>{specSeconds(duration_seconds)}</dd></div>
 </dl>;
}

export function DeliveryDimensionNote({spec}){
 if(!spec?.resolution)return null;
 if(spec.dimension_basis==='source_bound')return <p className="action-hint">沿用原片尺寸作为验收目标，该模型组合的精确输出尺寸待原始回片核实。</p>;
 if(spec.dimension_basis==='observed')return <p className="action-hint">目标宽高已有该模型和画幅的原始回片实测。</p>;
 if(spec.dimension_basis==='unverified')return <Alert tone="warning" role="note"><AlertDescription>当前模型、画幅和档位的精确宽高尚待核实，请查看预检兼容性。</AlertDescription></Alert>;
 return null;
}
