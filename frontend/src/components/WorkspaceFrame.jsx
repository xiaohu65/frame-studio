import React from 'react';
import { Clapperboard, Film, FolderOpen, Layers, ListChecks, Plus, Settings, Sparkles, Users, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectItem } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent,
  SidebarHeader, SidebarInset, SidebarMenu,
  SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarRail,
  SidebarTrigger, useSidebar,
} from '@/components/ui/sidebar';
import { cn } from '@/lib/utils';

const REPLACEMENT_LABELS = ['原片与分段', '人物替换', '场景与范围', '生成视频', '结果与导出'];
const STEP_ICONS = [Film, Users, Layers, Sparkles, ListChecks];

function WorkflowSidebar({ replacement, steps, step, onStepChange, configured, onOpenConfig }) {
  const { isMobile, setOpenMobile } = useSidebar();
  function selectStep(index) {
    if (isMobile) setOpenMobile(false);
    onStepChange(index);
  }
  function openConfig(event) {
    const returnTarget = isMobile ? document.querySelector('[data-sidebar="trigger"]') : event;
    if (isMobile) setOpenMobile(false);
    onOpenConfig(returnTarget || event);
  }
  function restoreSidebarFocus(event) {
    event.preventDefault();
    const active = document.activeElement;
    if (active === document.body || active?.closest('[data-sidebar="sidebar"][data-mobile="true"]')) {
      document.querySelector('[data-sidebar="trigger"]')?.focus({ preventScroll: true });
    }
  }
  return (
    <Sidebar collapsible="icon" onCloseAutoFocus={restoreSidebarFocus}>
      <SidebarHeader className="h-16 justify-center px-2">
        <div className="flex items-center gap-2">
          <SidebarMenu><SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild tooltip="帧工坊工作台">
              <a href="/" aria-label="帧工坊工作台">
                <span className="flex aspect-square size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                  <Clapperboard className="size-4" aria-hidden="true" />
                </span>
                <span className="truncate font-semibold group-data-[collapsible=icon]:hidden">帧工坊</span>
              </a>
            </SidebarMenuButton>
          </SidebarMenuItem></SidebarMenu>
          <Button type="button" variant="ghost" size="icon-sm" className="md:hidden" aria-label="关闭制作流程" onClick={() => setOpenMobile(false)}><X aria-hidden="true" /></Button>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup className="px-3 py-6 group-data-[collapsible=icon]:px-2">
          <SidebarGroupContent>
            <nav aria-label="制作流程"><SidebarMenu className="gap-2">
              {steps.map(([name], index) => {
                const Icon = STEP_ICONS[index] || Film;
                const label = replacement ? REPLACEMENT_LABELS[index] : name;
                return <SidebarMenuItem key={name}>
                  <SidebarMenuButton type="button" size="lg" isActive={step === index} tooltip={label} className="gap-3 px-3 group-data-[collapsible=icon]:justify-center"
                    aria-label={label} aria-current={step === index ? 'step' : undefined}
                    data-testid={`workflow-step-${index}`} onClick={() => selectStep(index)}>
                    <Icon aria-hidden="true" />
                    <span className="truncate group-data-[collapsible=icon]:hidden">{label}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>;
              })}
            </SidebarMenu></nav>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter className="px-3 pb-4 group-data-[collapsible=icon]:px-2"><SidebarMenu><SidebarMenuItem>
        <SidebarMenuButton type="button" size="lg" className="gap-3 px-3 group-data-[collapsible=icon]:justify-center" tooltip={configured ? 'API 设置 · 连接已配置' : 'API 设置 · 未配置连接'} onClick={openConfig} aria-label="API 设置" aria-describedby="studio-api-connection-state">
          <Settings aria-hidden="true" />
          <span className="truncate group-data-[collapsible=icon]:hidden">API 设置</span>
          <span id="studio-api-connection-state" className="sr-only">{configured ? '连接已配置' : '未配置连接'}</span>
        </SidebarMenuButton>
      </SidebarMenuItem></SidebarMenu></SidebarFooter>
      <SidebarRail aria-label="切换侧栏" title="切换侧栏" />
    </Sidebar>
  );
}

function WorkflowTrigger() {
  const { isMobile, openMobile, open } = useSidebar();
  const label = isMobile ? (openMobile ? '关闭制作流程' : '打开制作流程') : (open ? '收起侧栏' : '展开侧栏');
  return <SidebarTrigger aria-label={label} title={label} aria-expanded={isMobile ? openMobile : open} />;
}

export function WorkspaceFrame({ className, replacement, projects = [], activeId, projectCaption,
  onSelectProject, onOpenSeries, onNewProject, steps = [], step, onStepChange, onOpenConfig,
  configured, children, inspector, overlays }) {
  const selectedProject = projects.find(project => project.id === activeId);
  return (
    <SidebarProvider className={cn('app-shell [--studio-header-height:6.75rem] md:[--studio-header-height:4rem]', className)}>
      <WorkflowSidebar replacement={replacement} steps={steps} step={step} onStepChange={onStepChange} configured={configured} onOpenConfig={onOpenConfig} />
      <SidebarInset className="app-body min-w-0">
        <header className="app-header sticky top-0 z-20 grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 border-b bg-background px-4 py-3 md:flex md:h-16 md:gap-4 md:px-6 md:py-0">
          <WorkflowTrigger />
          <Separator orientation="vertical" className="hidden h-4 md:block" />
          <span className="text-sm font-semibold md:hidden">帧工坊</span>
          <div className="project-switch col-span-3 row-start-2 flex min-w-0 items-center gap-3 md:flex-1">
            <Select value={activeId || ''} onValueChange={onSelectProject} disabled={projects.length === 0}>
              <SelectTrigger className="w-full md:max-w-md" aria-label="当前项目" title={selectedProject?.name}><SelectValue placeholder={projects.length ? '选择项目' : '尚无项目'}>{selectedProject?.name}</SelectValue></SelectTrigger>
              <SelectContent position="popper"><SelectGroup>{projects.map(project => <SelectItem key={project.id} value={project.id}>{project.name}</SelectItem>)}</SelectGroup></SelectContent>
            </Select>
            {projectCaption && <span className="hidden shrink-0 text-xs text-muted-foreground lg:block">{projectCaption}</span>}
          </div>
          <div className="col-start-3 row-start-1 flex items-center gap-2">
            <Button type="button" variant="outline" onClick={onOpenSeries} aria-label="打开剧集与全剧角色库" title="剧集与角色库" data-testid="open-series-workspace"><FolderOpen data-icon="inline-start" aria-hidden="true" /><span className="hidden sm:inline">剧集与角色库</span></Button>
            <Button type="button" onClick={onNewProject} aria-label="新建项目"><Plus data-icon="inline-start" aria-hidden="true" /><span>新建项目</span></Button>
          </div>
        </header>
        <div className="workspace-layout flex min-w-0 flex-1 items-start"><div className="workspace">{children}</div>{inspector}</div>
      </SidebarInset>
      {overlays}
    </SidebarProvider>
  );
}
