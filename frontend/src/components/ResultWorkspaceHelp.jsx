import React from 'react';

import { Separator } from './ui/separator';


const SECTIONS = [
  ['接收与检查', 'API 回片自动接入；平台回片导入当前段。技术检查以实际文件为准，计费用量单独查看。'],
  ['对照验收', '同步对照原片画面与原声，技术检查通过后保存六项验收。更换候选或重新检查通过后须重新验收；字幕仅用于预览。'],
  ['完整成片', '全部片段验收并确认当前材料后，按原切点拼接原始回片、接回连续原声，导出无字幕 MP4。'],
];

export default function ResultWorkspaceHelp() {
  return (
    <div className="flex flex-col gap-6 result-support-help">
      {SECTIONS.map(([title, copy], index) => (
        <React.Fragment key={title}>
          {index > 0 && <Separator decorative />}
          
            <section className="flex flex-col gap-3">
              <h3 className="font-semibold leading-snug tracking-tight text-xl mb-0">{title}</h3>
              <p className="text-sm text-muted-foreground mb-0">{copy}</p>
            </section>
          
        </React.Fragment>
      ))}
    </div>
  );
}
