import React from 'react';

import { Separator } from './ui/separator';


const SECTIONS = [
  ['准备与确认', '准备生成会整理材料、制作分段并估算费用。核对材料与报价后，再单独授权本次上传与付费。'],
  ['草稿与版本', '切换片段会保留未保存提示词。按材料重建需确认，会覆盖并保存本段提示词；材料或设置变化后须重新准备。'],
  ['平台备用路径', '未配置 API 时可导出生成包，平台生成后在第 5 步导入。材料待确认的包也可下载；验收前须补齐材料确认。'],
  ['结果验收', '第 5 步检查原始回片规格并逐项验收。通过后按原切点拼接，并接回一次连续原声。'],
];

export default function GenerationWorkspaceHelp() {
  return (
    <div className="flex flex-col gap-6 generation-support-help">
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
