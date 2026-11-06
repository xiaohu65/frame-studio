import { feedbackVariant } from "@/components/ui/feedback";
import React, { useId, useState } from 'react';
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardAction } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { Table, TableHeader, TableRow, TableBody, TableCell, TableHead } from "@/components/ui/table";
import { ArrowRight, Plus } from 'lucide-react';
import { SeriesDisclosure, SeriesField, SeriesNotice, SeriesSelect } from './SeriesControls';
import './SeriesEpisodes.css';

const EPISODE_STATUS = {
  NEEDS_SOURCE: ['待导入原片', 'gray'],
  NEEDS_MAPPING: ['待绑定全剧角色', 'amber'],
  NEEDS_IDENTITY_REVIEW: ['待确认人物对应', 'amber'],
  NEEDS_LOCAL_CHECK: ['待本地检查与复核', 'amber'],
  LOCAL_REVIEWED: ['已记录本地复核', 'green'],
  MISSING: ['项目文件缺失', 'red'],
};

const FILTERS = [
  ['all', '全部集数'],
  ['source', '待导入'],
  ['mapping', '待绑定'],
  ['identity', '待确认'],
  ['review', '待复核'],
  ['updates', '有新版素材'],
];

function AttachEpisode({ project, series, linked, onAction, busy }) {
  const headingId = useId();
  const [number, setNumber] = useState(project?.series_link?.episode_number || 1);
  const [title, setTitle] = useState(project?.series_link?.title || project?.name || '');
  const elsewhere = project?.series_link?.series_id && project.series_link.series_id !== series.id;

  return (
    <section aria-labelledby={headingId}><Card className="series-episode-panel">
<CardHeader>
          <CardTitle><h2 id={headingId} className="font-semibold tracking-tight text-xl mb-0">把当前项目登记为一集</h2></CardTitle>
          <CardDescription><p className="text-sm text-muted-foreground mb-0">
            当前项目 <span className="font-medium text-muted-foreground">{project?.name || '请先建立本地项目'}</span>
            {linked && ` · 已登记为第${project.series_link.episode_number}集`}
          </p></CardDescription>
        </CardHeader>
<CardContent>
        {elsewhere ? (
          <SeriesNotice tone="warning">当前项目属于另一部剧。请切换到对应剧集管理。</SeriesNotice>
        ) : (
          <div className="series-episode-form">
            <SeriesField label="登记为第几集" ariaLabel="本项目的集数" type="number" min={1} max={10000} value={number} onChange={event => setNumber(Number(event.target.value))} />
            <SeriesField label="本集名称" ariaLabel="本集名称" maxLength={100} value={title} onChange={event => setTitle(event.target.value)} />
            <Button type="button" onClick={() => onAction('attach_episode', { episode_number: number, title })} variant={linked ? 'outline' : 'default'} size="lg" disabled={(busy === 'attach_episode') || (!project || !!busy || !Number.isInteger(number) || number < 1)} aria-busy={(busy === 'attach_episode') || undefined}>{(busy === 'attach_episode') && <Spinner />}
              {linked ? '保存集数登记' : '登记当前项目'}
            </Button>
          </div>
        )}
      </CardContent>
</Card></section>
  );
}

function BatchEpisodes({ onAction, busy }) {
  const [start, setStart] = useState(1);
  const [count, setCount] = useState(1);
  const valid = Number.isInteger(start) && Number.isInteger(count) && start >= 1 && count >= 1 && count <= 100;

  return (
    <SeriesDisclosure label="需要准备多集？批量建立空白项目（最多 100 集）" contentClassName="series-episode-batch-content">
      <p className="text-sm text-muted-foreground mb-0">
        仅建立本地空白项目，原片和人物仍需逐集处理。已有集数会保留。
      </p>
      <div className="series-episode-form series-episode-batch-form">
        <SeriesField label="批量起始集数" ariaLabel="批量起始集数" type="number" min={1} max={10000} value={start} onChange={event => setStart(Number(event.target.value))} />
        <SeriesField label="建立集数（最多100集）" ariaLabel="建立集数（最多100集）" type="number" min={1} max={100} value={count} onChange={event => setCount(Number(event.target.value))} />
        <Button type="button" onClick={() => onAction('create_episodes', { start, count })} variant="outline" size="lg" disabled={(busy === 'create_episodes') || (!!busy || !valid)} aria-busy={(busy === 'create_episodes') || undefined}>{(busy === 'create_episodes') && <Spinner />}
          <Plus size={16} aria-hidden="true" />建立第{start}至{start + count - 1}集工作区
        </Button>
      </div>
    </SeriesDisclosure>
  );
}

function EpisodeIssues({ episode }) {
  const issues = episode.issues || [];
  const blocking = issues.filter(issue => issue.blocking).length;

  if (!issues.length) {
    return (
      <span className="text-sm text-muted-foreground">
        {episode.status === 'NEEDS_SOURCE' ? '先导入本集原片' : '暂无全剧绑定缺项；镜头、动作与范围仍需本集检查'}
      </span>
    );
  }

  return (
    <SeriesDisclosure className="series-episode-issues" label={`${blocking} 项待处理 · ${issues.length - blocking} 项提示`}>
      <ul className="series-episode-issue-list">
        {issues.map((issue, index) => <li key={`${issue.code}-${index}`}>{issue.message}</li>)}
      </ul>
    </SeriesDisclosure>
  );
}

export function SeriesEpisodeTable({ episodes, onOpen, busy }) {
  return (
    <Table className="series-episode-table" data-testid="series-episode-table">
      <caption className="visually-hidden">各集角色对应与审核进度</caption>
      <colgroup>
        <col className="series-episode-col-name" />
        <col className="series-episode-col-count" />
        <col className="series-episode-col-state" />
        <col className="series-episode-col-issues" />
        <col className="series-episode-col-action" />
      </colgroup>
      <TableHeader>
        <TableRow>
          <TableHead>集数与项目</TableHead>
          <TableHead>人物与片段</TableHead>
          <TableHead>当前状态</TableHead>
          <TableHead>待核对事项</TableHead>
          <TableHead>操作</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {episodes.map(episode => {
          const [status, color] = EPISODE_STATUS[episode.status] || [episode.status, 'gray'];
          return (
            <TableRow key={episode.project_id}>
              <TableHead scope="row">
                <div className="flex flex-col gap-1">
                  <span className="text-sm font-medium">第{episode.episode_number}集</span>
                  <span className="text-sm text-muted-foreground font-normal">{episode.title || episode.project_name}</span>
                </div>
              </TableHead>
              <TableCell>{episode.actor_count || 0} 人 · {episode.segment_count || 0} 段</TableCell>
              <TableCell>
                <div className="flex flex-col items-start gap-1">
                  <Badge variant={feedbackVariant(color)}>{status}</Badge>
                  {episode.has_final && <span className="text-xs text-muted-foreground">已有本地成片，仍以当前审核记录为准</span>}
                </div>
              </TableCell>
              <TableCell><EpisodeIssues episode={episode} /></TableCell>
              <TableCell>
                <Button type="button" onClick={() => onOpen(episode.project_id)} variant="outline" size="lg" disabled={!!busy || episode.status === 'MISSING'}>
                  打开第{episode.episode_number}集<ArrowRight size={16} aria-hidden="true" />
                </Button>
              </TableCell>
            </TableRow>
          );
        })}
        {!episodes.length && (
          <TableRow>
            <TableCell colSpan={5}>
              <span className="text-sm text-muted-foreground">当前筛选下没有集数。可登记当前项目或建立各集工作区。</span>
            </TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  );
}

export default function SeriesEpisodes({ project, series, linked, onAction, busy, episodes, filter, onFilterChange, onOpen }) {
  const headingId = useId();

  return (
    <div className="series-episodes">
      <AttachEpisode key={`${project?.id}:${series.id}`} project={project} series={series} linked={linked} onAction={onAction} busy={busy} />
      <BatchEpisodes onAction={onAction} busy={busy} />
      <section aria-labelledby={headingId}><Card className="series-episode-panel">
<CardHeader>
            <div className="flex flex-col gap-2">
              <CardTitle><h2 id={headingId} className="font-semibold tracking-tight text-xl mb-0">每集处理状态</h2></CardTitle>
              <CardDescription><p className="text-sm text-muted-foreground mb-0">本地复核记录对应本集当前材料。生成结果仍须逐段验收。</p></CardDescription>
            </div>
            <CardAction><SeriesSelect label="筛选集数状态" ariaLabel="筛选集数状态" value={filter} onValueChange={onFilterChange} options={FILTERS} /></CardAction>
          </CardHeader>
<CardContent>
          <SeriesEpisodeTable episodes={episodes} onOpen={onOpen} busy={busy} />
        </CardContent>
</Card></section>
    </div>
  );
}
