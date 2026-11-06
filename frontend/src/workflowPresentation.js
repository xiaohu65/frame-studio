import {generationWorkflowStatus,resultWorkflowStatus} from './workflowStatus.js';

// These labels describe saved records only; they never approve a workflow.
export const REPLACEMENT_STEPS = [
  ['导入原片与确认分段', '确定视频、分段和交付档位'],
  ['指定人物如何替换', '原片人物 → 新角色'],
  ['设置场景与替换范围', '明确可改与保留的区域'],
  ['生成视频', '确认设置与费用、生成候选视频'],
  ['复核结果并导出成片', '检查替换结果、接回原声'],
];

export const STEP_TASKS = [
  ['导入原片，确认每段起止位置，并选定本批唯一交付档位。', '已保存的原片、分段范围和交付档位'],
  ['逐人指定原视频中的谁，要换成哪位新角色，并核对不同镜头中的身份。', '人物对应、参考图与出现位置记录'],
  ['确定场景是否更换，再为每个片段制作或导入替换范围，保护原有遮挡。', '替换范围与前景保护范围'],
  ['确认任务与费用，生成并保存各片段视频，再进入结果复核。', '已保存的生成视频与任务记录'],
  ['逐段检查原始回片的技术规格和实际效果。通过后按原切点拼接，接回连续原声。', '对照预览与正式无字幕母版'],
];

export function workflowRecordLabels(project,generation=null,options={}) {
  const segments = project.segments || [];
  const actors = project.source_actors || [];
  return [
    project.source ? `${segments.length} 段已保存` : '等待导入原片',
    actors.length ? `已记录 ${actors.length} 人` : '等待指定人物',
    project.scene?.mode === 'replace' ? '替换场景外观' : '保留原场景',
    generationWorkflowStatus(project,generation,options).label,
    resultWorkflowStatus(project,generation,options).label,
  ];
}

export function stepScope(step) {
  if (step === 0) return ['处理范围：整个原片', '每个片段使用这里保存的原片起止位置。'];
  if (step === 1) return ['处理范围：整个原片', '人物编号在所有片段中保持一致。'];
  if (step === 2) return ['场景设置用于全片 · 替换范围按片段处理', '切换下方片段，分别检查对应范围。'];
  if (step === 3) return ['生成按本次任务片段执行', '先准备已保存材料，再集中确认六项要求与本次费用。'];
  return ['结果按片段复核 · 最后导出完整视频', '当前段复核后，继续处理其他片段。'];
}
