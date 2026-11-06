import React, { useEffect, useRef, useState } from 'react';
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle, EmptyDescription } from "@/components/ui/empty";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardAction } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogTitle, DialogDescription, DialogClose } from "@/components/ui/dialog";
import { BookOpen, Layers, Plus, RefreshCw, Users, X } from 'lucide-react';
import { request } from '../api';
import SeriesEpisodes from './SeriesEpisodes';
import SeriesCharacters from './SeriesCharacters';
import SeriesBindings from './SeriesBindings';
import { SeriesDisclosure, SeriesField, SeriesNotice, SeriesSelect } from './SeriesControls';
import './SeriesWorkspace.css';

const TABS = [
  ['episodes', '集数总览', Layers],
  ['characters', '全剧角色档案', BookOpen],
  ['bindings', '当前项目人物对应', Users],
];

const RELATIONSHIPS = [
  ['全剧角色档案', '记录原剧中的同一个角色'],
  ['固定素材版本', '指定要换成的新造型与参考图'],
  ['每集人物对应', '在各集绑定人物，再逐人确认'],
];

const STATS = [
  ['episodes', '已登记集数'],
  ['needs_mapping', '待绑定全剧角色'],
  ['needs_confirmation', '待身份确认'],
  ['version_updates', '有新版素材提示'],
];

const FILTER_STATUS = {
  source: 'NEEDS_SOURCE',
  mapping: 'NEEDS_MAPPING',
  identity: 'NEEDS_IDENTITY_REVIEW',
  review: 'NEEDS_LOCAL_CHECK',
};

const seriesPath = id => '/api/series/' + encodeURIComponent(id);

export function filterEpisodes(episodes, filter) {
  if (filter === 'all') return episodes;
  if (filter === 'updates') {
    return episodes.filter(episode => episode.issues?.some(issue => issue.code === 'SERIES_NEW_VERSION_AVAILABLE'));
  }
  return episodes.filter(episode => episode.status === FILTER_STATUS[filter]);
}

export function SeriesWorkspace({ project, onProjectRefresh, onSelectProject, onClose, returnFocus }) {
  const projectLink = project?.series_link;
  const [catalog, setCatalog] = useState([]);
  const [seriesId, setSeriesId] = useState(projectLink?.series_id || '');
  const [dashboard, setDashboard] = useState(null);
  const [tab, setTab] = useState('episodes');
  const [newName, setNewName] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [dashboardLoading, setDashboardLoading] = useState(!!seriesId);
  const [filter, setFilter] = useState('all');

  const activeSeries = useRef(seriesId);
  const live = useRef(true);
  const pending = useRef(false);
  const closeButton = useRef(null);
  const series = dashboard?.series;
  const loading = catalogLoading || dashboardLoading;
  const linked = !!series && projectLink?.series_id === series.id;
  const catalogLocked = !!busy || catalogLoading;

  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);

  function chooseSeries(id) {
    if (activeSeries.current === id) return;
    activeSeries.current = id;
    setSeriesId(id);
    setDashboard(null);
    setDashboardLoading(!!id);
    setFilter('all');
    setError('');
  }

  async function loadCatalog(signal) {
    const response = await request('/api/series', { signal });
    const items = Array.isArray(response) ? response : response.series || [];
    if (live.current && !signal?.aborted) setCatalog(items);
    return items;
  }

  async function loadDashboard(id, signal) {
    if (!id) return;
    const details = await request(seriesPath(id) + '/dashboard', { signal });
    if (live.current && !signal?.aborted && activeSeries.current === id) setDashboard(details);
  }

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    loadCatalog(signal).then(items => {
      if (!signal.aborted && !activeSeries.current && items.length) chooseSeries(items[0].id);
    }).catch(reason => {
      if (!signal.aborted) setError(reason.message);
    }).finally(() => {
      if (!signal.aborted) setCatalogLoading(false);
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!seriesId) return;
    const controller = new AbortController();
    const { signal } = controller;
    loadDashboard(seriesId, signal).catch(reason => {
      if (!signal.aborted && activeSeries.current === seriesId) setError(reason.message);
    }).finally(() => {
      if (!signal.aborted && activeSeries.current === seriesId) setDashboardLoading(false);
    });
    return () => controller.abort();
  }, [seriesId]);

  async function run(label, operation) {
    if (pending.current) return null;
    pending.current = true;
    setBusy(label);
    setError('');
    setNotice('');
    try {
      return await operation();
    } catch (reason) {
      if (live.current) setError(reason.message);
      return null;
    } finally {
      pending.current = false;
      if (live.current) setBusy('');
    }
  }

  function action(name, body = {}) {
    if (!series) return;
    return run(name, async () => {
      const result = await request(seriesPath(series.id) + '/actions', {
        method: 'POST',
        body: {
          action: name,
          project_id: project?.id,
          expected_revision: series.revision,
          expected_project_revision: project?.revision,
          ...body,
        },
      });
      await Promise.all([loadDashboard(series.id), loadCatalog(), onProjectRefresh?.()]);
      if (live.current) setNotice(result.message || '已保存到本机剧集');
      return result;
    });
  }

  function refresh() {
    return run('refresh', async () => {
      const [, items] = await Promise.all([loadDashboard(seriesId), loadCatalog(), onProjectRefresh?.()]);
      if (!live.current) return;
      if (!activeSeries.current && items.length) chooseSeries(items[0].id);
      setNotice('已读取最新剧集与当前项目状态。');
    });
  }

  function create(event) {
    event.preventDefault();
    const name = newName.trim();
    if (!name || catalogLocked) return;
    return run('create_series', async () => {
      const result = await request('/api/series', { method: 'POST', body: { name } });
      const created = result.series || result;
      if (!live.current) return;
      chooseSeries(created.id);
      setNewName('');
      setTab('episodes');
      await loadCatalog();
      if (live.current) setNotice('剧集已建立并保存在本机。接下来把当前项目登记为一集，或批量建立空白项目。');
    });
  }

  function close() {
    if (!pending.current) onClose?.();
  }

  function restoreFocus(event) {
    event.preventDefault();
    const opener = returnFocus?.current;
    const target = opener?.isConnected ? opener : document.querySelector('[data-testid="open-series-workspace"]');
    target?.focus({ preventScroll: true });
  }

  const projectSeries = catalog.find(item => item.id === projectLink?.series_id);

  return (
    <Dialog open onOpenChange={open => { if (!open) close(); }}>
      <DialogContent className="series-workspace" data-testid="series-workspace" onOpenAutoFocus={event => { event.preventDefault(); closeButton.current?.focus(); }} onCloseAutoFocus={restoreFocus} onInteractOutside={event => event.preventDefault()} onEscapeKeyDown={event => { if (pending.current) event.preventDefault(); }} showCloseButton={false} style={{ maxWidth: "calc(100vw - 48px)" }}>
        <header className="series-header">
          <div>
            <DialogTitle>剧集与角色库</DialogTitle>
            <DialogDescription>管理各集项目、全剧角色和固定造型版本。</DialogDescription>
          </div>
          <DialogClose asChild>
            <Button ref={closeButton} type="button" aria-label="关闭剧集与角色库" variant="ghost" size="icon" disabled={!!busy}><X size={21} aria-hidden="true" /></Button>
          </DialogClose>
        </header>

        <div className="series-content">
          <section aria-label="当前项目与剧集"><Card className="series-context">
<CardHeader>
                <div className="flex items-center gap-3">
                  <Badge variant="secondary">当前项目</Badge>
                  <CardTitle><h3 className="font-semibold tracking-tight text-lg mb-0">{project?.name || '尚未选择项目'}</h3></CardTitle>
                </div>
                <span className="text-sm text-muted-foreground">
                  {projectLink
                    ? (projectSeries?.name || '已关联剧集') + ' · 第 ' + projectLink.episode_number + ' 集'
                    : '独立项目 · 尚未登记为集数'}
                </span>
              </CardHeader>
<CardContent>
              <div className="series-catalog-grid">
                <div className="series-catalog-form">
                  <SeriesSelect label="选择已有剧集" ariaLabel="当前管理的剧集" value={seriesId} disabled={catalogLocked || !catalog.length} onValueChange={chooseSeries} options={[
                      ['', catalog.length ? '请选择剧集' : '尚未建立剧集'],
                      ...catalog.map(item => [item.id, item.name + ' · ' + (item.episode_count || 0) + ' 集']),
                    ]} />
                  <Button type="button" onClick={refresh} variant="outline" size="lg" disabled={(busy === 'refresh') || (catalogLocked)} aria-busy={(busy === 'refresh') || undefined}>{(busy === 'refresh') && <Spinner />}<RefreshCw size={16} aria-hidden="true" />刷新剧集状态</Button>
                </div>
                <form className="series-catalog-form" onSubmit={create}>
                  <SeriesField label={catalog.length ? '建立新剧集' : '建立第一部剧集'} ariaLabel="新剧集名称" value={newName} onChange={event => setNewName(event.target.value)} placeholder="例如：某剧人物重制" maxLength={100} disabled={catalogLocked} />
                  <Button type="submit" variant={catalog.length ? 'outline' : 'default'} size="lg" disabled={(busy === 'create_series') || (catalogLocked || !newName.trim())} aria-busy={(busy === 'create_series') || undefined}>{(busy === 'create_series') && <Spinner />}
                    <Plus size={16} aria-hidden="true" />建立剧集
                  </Button>
                </form>
              </div>
            </CardContent>
</Card></section>

          {error && <SeriesNotice tone="destructive" role="alert">{error} 请按提示处理后重试。如提示版本冲突，请先刷新状态。</SeriesNotice>}
          {notice && <SeriesNotice role="status">{notice}</SeriesNotice>}

          {series && <dl className="series-relationship" aria-label="全剧角色与每集人物的关系">
            {RELATIONSHIPS.map(([title, description]) => (
              <div key={title}>
                <dt className="text-sm font-bold">{title}</dt>
                <dd className="text-sm text-muted-foreground">{description}</dd>
              </div>
            ))}
          </dl>}

          <SeriesDisclosure label="跨集一致性需要核对哪些内容？">
            <p className="text-sm text-muted-foreground mb-0">
              每集都要确认原片人物对应哪个全剧角色。人物换装、少年与成年、分饰多角、遮挡和武打镜头仍须逐处核对。总览统计已保存的绑定和审核记录，生成结果仍需逐段验收。
            </p>
          </SeriesDisclosure>

          {loading ? <div role="status" className="series-loading flex items-center justify-center gap-3">
            <Spinner /><span className="text-sm text-muted-foreground">正在读取剧集…</span>
          </div> : series ? <>
            <dl className="series-stat-grid" aria-label="剧集统计">
              {STATS.map(([key, label]) => (
                <div key={key}>
                  <dt className="text-sm text-muted-foreground">{label}</dt>
                  <dd className="font-semibold tracking-tight text-2xl mb-0">{dashboard.counts?.[key] || 0}</dd>
                </div>
              ))}
            </dl>
            <Tabs value={tab} onValueChange={setTab}>
              <TabsList className="series-tabs" aria-label="剧集管理内容">
                {TABS.map(([id, label, Icon]) => (
                  <TabsTrigger key={id} value={id}><Icon size={16} aria-hidden="true" />{label}</TabsTrigger>
                ))}
              </TabsList>
              <TabsContent value="episodes">
                <SeriesEpisodes project={project} series={series} linked={linked} onAction={action} busy={busy} episodes={filterEpisodes(dashboard.episodes || [], filter)} filter={filter} onFilterChange={setFilter} onOpen={async id => { await onSelectProject?.(id); onClose?.(); }} />
              </TabsContent>
              <TabsContent value="characters">
                <SeriesCharacters project={project} series={series} linked={linked} onAction={action} busy={busy} />
              </TabsContent>
              <TabsContent value="bindings">
                <SeriesBindings project={project} series={series} linked={linked} onAction={action} busy={busy} />
              </TabsContent>
            </Tabs>
          </> : !error && <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon"><BookOpen aria-hidden="true" /></EmptyMedia>
              <EmptyTitle>从建立剧集开始</EmptyTitle>
              <EmptyDescription>先填写剧集名称，再把本地项目登记为各集，保存共用造型并逐集确认人物对应。只制作单条视频时，可直接返回当前项目。</EmptyDescription>
            </EmptyHeader>
          </Empty>}
        </div>

        <footer className="series-footer">
          <p className="text-sm text-muted-foreground mb-0">发布新版本后，各集继续使用已选定的造型。需要升级的集数须重新绑定并确认。</p>
          <Button type="button" onClick={close} variant="outline" size="lg" disabled={!!busy}>返回当前项目</Button>
        </footer>
      </DialogContent>
    </Dialog>
  );
}

export default SeriesWorkspace;
export { SeriesEpisodeTable } from './SeriesEpisodes';
export { SeriesCharacterCard } from './SeriesCharacters';
export { SeriesActorBinding } from './SeriesBindings';
