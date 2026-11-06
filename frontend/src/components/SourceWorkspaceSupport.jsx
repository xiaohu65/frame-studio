import { feedbackVariant } from "@/components/ui/feedback";
import React from 'react';
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from "@/components/ui/collapsible";
import { AlertCircle, Check, ChevronDown, Clock3, ExternalLink } from 'lucide-react';
import { actionName, jobFeedback } from './Common';
import './SourceWorkspaceSupport.css';

function SourceSupportDisclosure({ label, className, children, compact = false }) {
  return (
    <Collapsible className={className}>
      <CollapsibleTrigger asChild>
        <Button type="button" className={['source-support-toggle h-auto w-full justify-between whitespace-normal py-2 text-start', compact && 'px-0 has-[>svg]:px-0'].filter(Boolean).join(' ')} variant="ghost" size="lg">
          {label}
          <ChevronDown className="source-support-chevron" size={18} aria-hidden="true" />
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="source-support-expanded">
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}

function SourceJobResult({ result, compact = false }) {
  const download = result?.url || result?.asset?.url;
  const technical = typeof result === 'string' ? result : JSON.stringify(result, null, 2);

  if (compact) return (
    <div className="source-job-result">
      {download && <Button asChild variant="link" size="sm" className="justify-start px-0 has-[>svg]:px-0">
        <a href={download} download><ExternalLink data-icon="inline-start" aria-hidden="true" />查看或下载处理结果</a>
      </Button>}
      <pre className="source-job-json text-xs"><code>{technical}</code></pre>
    </div>
  );

  return (
    <>
      {download && (
        <a href={download} download className="source-job-download text-primary underline-offset-4 hover:underline text-base">
          <ExternalLink size={16} aria-hidden="true" />查看或下载处理结果
        </a>
      )}
      <SourceSupportDisclosure label="查看处理详情" className="source-job-technical">
        <pre className="source-job-json"><code>{technical}</code></pre>
      </SourceSupportDisclosure>
    </>
  );
}

function SourceJobRecord({ job, project, compact = false }) {
  const feedback = jobFeedback(job, project);
  const Icon = job.status === 'QUEUED' ? Clock3 : ['warning', 'error'].includes(feedback.tone) ? AlertCircle : Check;
  const badgeColor = feedback.tone === 'error' ? 'red' : feedback.tone === 'warning' ? 'amber' : 'gray';
  const extraMessage = job.message !== feedback.detail && job.message;

  return (
    <article className="source-job-record">
      <div className="source-job-label">
        <span className="source-job-icon" data-tone={feedback.tone} aria-hidden="true">
          {job.status === 'RUNNING' ? <Spinner /> : <Icon size={18} />}
        </span>
        <div className="source-job-copy">
          <span className={compact ? 'text-sm font-medium' : 'text-base font-medium'}>{actionName(job.action)}{job.segment_id ? ` · ${job.segment_id}段` : ''}</span>
          <span className="text-sm text-muted-foreground">{feedback.detail}</span>
          {feedback.outdated && <span className="text-sm text-warning">旧版本记录，仅供查看；请以当前材料检查为准。</span>}
        </div>
        <Badge data-tone={feedback.tone} className="source-job-status" variant={feedbackVariant(badgeColor)}>{feedback.label}</Badge>
      </div>
      {(extraMessage || job.result) && (
        compact ? <SourceSupportDisclosure label="查看记录详情" className="source-job-extra" compact>
          <div className="source-job-extra-content">
            {extraMessage && <p className="text-sm">{extraMessage}</p>}
            {job.result && <SourceJobResult result={job.result} compact />}
          </div>
        </SourceSupportDisclosure> : <div className="source-job-details flex flex-col gap-2">
          {extraMessage && <p className="text-base mb-0">{extraMessage}</p>}
          {job.result && <SourceJobResult result={job.result} />}
        </div>
      )}
    </article>
  );
}

export function SourceWorkspaceHistory({ project, jobs, compact = false }) {
  const visible = jobs.slice(0, 8);
  const running = visible.filter(job => ['RUNNING', 'QUEUED'].includes(job.status));
  const finished = visible.filter(job => !['RUNNING', 'QUEUED'].includes(job.status));

  if (compact) return (
    <section aria-label="处理进度与记录" className="source-support-history-compact">
      {visible.length > 0 && <p className="text-sm text-muted-foreground">本机任务 · 不代表已提交 Wan</p>}
      {!visible.length ? <p className="text-sm text-muted-foreground">保存材料并执行本地操作后，这里会显示处理进度、完成结果和失败原因。</p> : (
        <div aria-live="polite" className="source-step-job-list">
          {[...running, ...finished].map(job => <SourceJobRecord key={job.id} job={job} project={project} compact />)}
        </div>
      )}
    </section>
  );

  return (
    <section aria-label="处理进度与记录"><Card className="source-support-history">
<CardHeader>
          <CardTitle><h3 className="font-semibold tracking-tight text-xl mb-0">处理进度与记录</h3></CardTitle>
          {visible.length > 0 && <CardDescription>本机任务 · 不代表已提交 Wan</CardDescription>}
        </CardHeader>
<CardContent>
        {!visible.length ? (
          <p className="text-sm text-muted-foreground mb-0">保存材料并执行本地操作后，这里会显示处理进度、完成结果和失败原因。</p>
        ) : (
          <div aria-live="polite">
            {[...running, ...finished].map(job => <SourceJobRecord key={job.id} job={job} project={project} />)}
          </div>
        )}
      </CardContent>
</Card></section>
  );
}
