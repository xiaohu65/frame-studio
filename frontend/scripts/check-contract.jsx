import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {SourceStage,PuppetStage,AssetsStage,GenerateStage,MusicStage} from '../src/components/Stages';
import {Inspector,ConfigDrawer} from '../src/components/Inspector';
import {MediaPreview,SegmentList,Timeline} from '../src/components/MediaWorkspace';
import {JobList} from '../src/components/Common';
const s=await (await fetch('http://127.0.0.1:8765/api/state')).json();
const p=s.project,seg=p.segments[0];
const noop=()=>{};
for(const [name,C] of Object.entries({SourceStage,PuppetStage,AssetsStage,GenerateStage,MusicStage,Inspector,ConfigDrawer,MediaPreview,SegmentList,Timeline,JobList})){
 const html=renderToStaticMarkup(<C project={p} segment={seg} config={s.config} system={s.system} jobs={s.jobs} segments={p.segments} selectedId={seg.id} mode="puppet" onPatch={noop} onAction={noop} onUpload={noop} onImport={noop} onSelect={noop} setMode={noop}/>);
 if(!html.length)throw new Error(name+' empty');
 if(html.includes('NaN'))throw new Error(name+' invalid numeric output');
 console.log(name+': '+html.length+' HTML characters rendered from actual project');
}
