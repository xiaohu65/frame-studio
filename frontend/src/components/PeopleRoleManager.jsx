import {Collapsible as CollapsibleRoot,CollapsibleTrigger,CollapsibleContent} from "./ui/collapsible";
import React, { useEffect, useId, useRef, useState } from 'react';
import { Button as UiButton } from './ui/button';
import { Spinner } from './ui/spinner';

import { Input } from './ui/input';
import { Textarea } from './ui/textarea';
import { Badge } from './ui/badge';
import { Dialog, DialogContent, DialogTitle, DialogClose, DialogDescription } from './ui/dialog';
import { Alert, AlertDescription } from './ui/alert';
import { Field, FieldGroup, FieldLabel } from './ui/field';


import { AlertCircle, ChevronDown, FolderInput, Image, Plus, Save, Trash2, Upload, X } from 'lucide-react';
import { assetUrl } from '../api';
import useDraft from '../useDraft';
import './PeopleRoleManager.css';

const REFERENCE_TYPES = 'image/png,image/jpeg,image/webp,image/bmp';

function RoleReferenceActions({ role, disabled, onUpload, onImport }) {
  const inputRef = useRef(null);
  const pathTrigger = useRef(null);
  const restorePathFocus = useRef(false);
  const pathId = useId();
  const [uploading, setUploading] = useState(false);
  const [pathOpen, setPathOpen] = useState(false);
  const [path, setPath] = useState('');
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState('');
  const locked = disabled || uploading || importing;

  useEffect(() => {
    if (!pathOpen && !locked && restorePathFocus.current) {
      restorePathFocus.current = false;
      pathTrigger.current?.focus({ preventScroll: true });
    }
  }, [locked, pathOpen]);

  async function upload(event) {
    const input = event.currentTarget;
    const files = Array.from(input.files);
    if (!files.length) return;
    setUploading(true);
    try {
      await onUpload('reference', files, { role: role.id });
    } finally {
      input.value = '';
      setUploading(false);
    }
  }

  function closePath() {
    restorePathFocus.current = true;
    setPathOpen(false);
  }

  async function importPath(event) {
    event.preventDefault();
    const value = path.trim();
    if (!value || locked) return;
    setImporting(true);
    setError('');
    try {
      const result = await onImport({ path: value, kind: 'reference', role: role.id });
      if (result === null) {
        setError('文件未能导入。请查看错误说明，修正路径后重试。');
        return;
      }
      setPath('');
      closePath();
    } catch (reason) {
      setError(reason?.message || '文件未能导入，请检查路径后重试。');
    } finally {
      setImporting(false);
    }
  }

  return (
    <CollapsibleRoot open={pathOpen} onOpenChange={setPathOpen}>
      <div className="people-role-actions">
        <input ref={inputRef} type="file" accept={REFERENCE_TYPES} multiple hidden disabled={locked} onChange={upload} />
        <UiButton type="button" onClick={() => inputRef.current?.click()} variant="outline" disabled={Boolean(uploading) || (disabled || importing)} aria-busy={(uploading) || undefined}>{(uploading) && <Spinner data-icon="inline-start" aria-hidden="true" />}
          <Upload aria-hidden="true" data-icon="inline-start" />导入参考图
        </UiButton>
        <CollapsibleTrigger asChild>
          <UiButton ref={pathTrigger} type="button" disabled={locked} variant="ghost">
            <FolderInput aria-hidden="true" data-icon="inline-start" />本机路径
            <ChevronDown className="people-role-arrow" aria-hidden="true" data-icon="inline-end" />
          </UiButton>
        </CollapsibleTrigger>
      </div>
      <CollapsibleContent className="people-role-path">
        <form onSubmit={importPath}>
          <FieldGroup className="people-role-inline-form">
          <Field>
            <FieldLabel htmlFor={pathId}>参考图绝对路径</FieldLabel>
            <Input id={pathId} aria-label={`${role.name}参考图绝对路径`} aria-describedby={`${pathId}-hint`} value={path} onChange={event => { setPath(event.target.value); setError(''); }} placeholder="例如：/Users/你的名字/Pictures/角色.png" disabled={locked} required />
          </Field>
          <UiButton type="submit" disabled={Boolean(importing) || (disabled || !path.trim())} aria-busy={(importing) || undefined}>{(importing) && <Spinner data-icon="inline-start" aria-hidden="true" />}导入</UiButton>
          <UiButton type="button" aria-label="取消导入" disabled={locked} onClick={closePath} variant="ghost" size="icon">
            <X aria-hidden="true" data-icon="inline-start" />
          </UiButton>
          </FieldGroup>
        </form>
        <p id={`${pathId}-hint`} className="text-sm text-muted-foreground">请保留原文件及其位置。</p>
        {error && <p role="alert" className="text-sm text-destructive mb-0">{error}</p>}
      </CollapsibleContent>
    </CollapsibleRoot>
  );
}

function RoleReferenceTile({ asset, label, disabled, onRemove }) {
  return (
    <div className="people-role-reference">
        <a href={assetUrl(asset)} target="_blank" rel="noreferrer" aria-label={`打开${asset.name || label}`} title={asset.name} className="people-role-reference-image">
          <img src={asset.thumbnail_url || assetUrl(asset)} alt={label} loading="lazy" />
        </a>
        <UiButton type="button" className="people-role-remove-reference" aria-label={`移除${asset.name || label}`} disabled={disabled} onClick={onRemove} variant="destructive" size="icon-sm">
          <Trash2 aria-hidden="true" data-icon="inline-start" />
        </UiButton>
    </div>
  );
}

function TargetRole({ role, project, onPatch, onUpload, onImport, bound, busy }) {
  const fieldsId = useId();
  const [draft, set, dirty, save, reset] = useDraft(role, project.id + role.id, project.revision);
  const references = role.images || [];

  function saveRole() {
    return save(onPatch, { roles: project.roles.map(item => item.id === role.id ? { ...draft, images: item.images } : item) });
  }

  function removeReference(id) {
    return onPatch({ roles: project.roles.map(item => item.id === role.id
      ? { ...item, images: item.images.filter(image => image.id !== id) } : item) });
  }

  return (
    <section className="people-target-role" data-testid={`target-role-${role.id}`}>
      <FieldGroup className="people-role-profile gap-5">
        <Field>
          <FieldLabel htmlFor={`${fieldsId}-name`}>角色名称</FieldLabel>
          <Input id={`${fieldsId}-name`} aria-label={`${role.id}目标角色名称`} value={draft.name} onChange={event => set(current => ({ ...current, name: event.target.value }))} />
        </Field>
        <Field>
          <FieldLabel htmlFor={`${fieldsId}-description`}>外观说明</FieldLabel>
          <Textarea id={`${fieldsId}-description`} aria-label={`${role.id}外观说明`} rows={3} value={draft.description || ''} onChange={event => set(current => ({ ...current, description: event.target.value }))} placeholder="面孔、发型、头饰、服装及必须保持的外观细节" />
        </Field>
        {dirty && <div className="people-role-save-row">
          <span role="status" className="text-sm text-warning">资料未保存；保存后可导入参考图。</span>
          <div className="people-role-actions">
            <UiButton type="button" onClick={reset} variant="outline">放弃修改</UiButton>
            <UiButton type="button" disabled={!dirty || !draft.name.trim() || busy} onClick={saveRole}>
              <Save aria-hidden="true" data-icon="inline-start" />保存角色资料
            </UiButton>
          </div>
        </div>}
      </FieldGroup>
      <section className="people-role-reference-section" aria-labelledby={`${fieldsId}-references`}>
        <div className="people-role-reference-heading">
          <h3 id={`${fieldsId}-references`} className="text-sm font-medium">参考图</h3>
        </div>
        {references.length > 0 && (
          <div className="people-role-reference-grid">
            {references.map(asset => (
              <RoleReferenceTile key={asset.id} asset={asset} label={`${role.name}参考图`}
                disabled={busy} onRemove={() => removeReference(asset.id)} />
            ))}
          </div>
        )}
        <RoleReferenceActions role={role} disabled={dirty || busy} onUpload={onUpload} onImport={onImport} />
      </section>
      <CollapsibleRoot className="people-role-more">
        <CollapsibleTrigger asChild>
          <UiButton type="button" className="people-role-disclosure h-auto whitespace-normal py-2 text-start" variant="ghost">
            更多操作<ChevronDown className="people-role-arrow" aria-hidden="true" data-icon="inline-end" />
          </UiButton>
        </CollapsibleTrigger>
        <CollapsibleContent className="people-role-more-content">
          {bound.length > 0 && <p className="text-sm text-muted-foreground">已保存对应：{bound.map(actor => actor.label || actor.id).join('、')}。删除前请先解除绑定。</p>}
          <UiButton type="button" onClick={() => onPatch({ roles: project.roles.filter(item => item.id !== role.id) })} disabled={bound.length > 0 || dirty || busy} variant="destructive">
            <Trash2 aria-hidden="true" data-icon="inline-start" />删除{role.name}
          </UiButton>
        </CollapsibleContent>
      </CollapsibleRoot>
    </section>
  );
}

export default function PeopleRoleManager({
  project, initialRoleId, focusCreate = false, onCreate, onChooseRole, onClose,
  onPatch, onUpload, onImport, onSeries, returnFocus, busy = false, error, draftRoleIds = [],
}) {
  const closeButton = useRef(null);
  const nameInput = useRef(null);
  const currentRole = useRef(null);
  const focusCreated = useRef(null);
  const previousFocus = useRef(null);
  const handoffToSeries = useRef(false);
  const createNameId = useId();
  const roles = project.roles || [];
  const [selectedId, setSelectedId] = useState(initialRoleId || roles[0]?.id || '');
  const [createOpen, setCreateOpen] = useState(focusCreate || roles.length === 0);
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(false);
  const [localError, setLocalError] = useState('');
  const role = roles.find(item => item.id === selectedId) || roles[0];
  const bound = (project.source_actors || []).filter(actor => actor.target_role_id === role?.id);
  const locked = busy || creating;

  useEffect(() => {
    if (createOpen) nameInput.current?.focus();
  }, [createOpen]);

  useEffect(() => {
    if (!creating && role?.id === focusCreated.current) {
      currentRole.current?.focus({ preventScroll: true });
      focusCreated.current = null;
    }
  }, [role?.id, creating]);

  async function create(event) {
    event.preventDefault();
    if (locked) return;
    setCreating(true);
    setLocalError('');
    try {
      const id = await onCreate(name);
      if (id) {
        focusCreated.current = id;
        setSelectedId(id);
        setName('');
        setCreateOpen(false);
      } else {
        setLocalError('未能新建角色，请按提示修正后重试。');
      }
    } catch (reason) {
      setLocalError(reason?.message || '未能新建角色，请重试。');
    } finally {
      setCreating(false);
    }
  }

  function openSeries() {
    if (locked) return;
    handoffToSeries.current = true;
    const opener = returnFocus && 'current' in returnFocus ? returnFocus.current : returnFocus;
    onClose();
    onSeries?.(opener);
  }

  function choose() {
    if (locked || !role) return;
    onChooseRole?.(role.id);
    onClose();
  }

  function openFocus(event) {
    event.preventDefault();
    previousFocus.current = event.currentTarget?.ownerDocument.activeElement;
    (createOpen ? nameInput : closeButton).current?.focus();
  }

  function closeFocus(event) {
    event.preventDefault();
    if (handoffToSeries.current) return;
    const opener = returnFocus && 'current' in returnFocus ? returnFocus.current : returnFocus;
    const target = opener?.isConnected ? opener : previousFocus.current;
    if (target?.isConnected && !target.matches(':disabled')) target.focus({ preventScroll: true });
  }

  return (
    <Dialog open onOpenChange={open => { if (!open && !locked) onClose(); }}>
      <DialogContent className="people-role-dialog w-[960px] max-w-[calc(100vw-48px)] gap-0 overflow-hidden p-0 sm:max-w-[calc(100vw-48px)]" onOpenAutoFocus={openFocus} onCloseAutoFocus={closeFocus} onEscapeKeyDown={event => { if (locked) event.preventDefault(); }} onInteractOutside={event => { if (locked) event.preventDefault(); }} showCloseButton={false}>
        <header className="people-role-heading">
          <div className="flex flex-col gap-2">
            <DialogTitle>角色与参考图</DialogTitle>
            <DialogDescription>角色资料需保存；参考图导入和移除即时生效。</DialogDescription>
          </div>
          <DialogClose asChild>
            <UiButton ref={closeButton} type="button" aria-label="关闭角色与参考图" disabled={locked} variant="ghost" size="icon">
              <X aria-hidden="true" data-icon="inline-start" />
            </UiButton>
          </DialogClose>
        </header>
        <div className="people-role-body">
        {(error || localError) && (
          <Alert className="people-role-error" role="alert" data-tone={"destructive"} variant="destructive">
            <AlertCircle size={17} aria-hidden="true" />
            <AlertDescription>{error || localError}</AlertDescription>
          </Alert>
        )}
        <div className="people-role-layout">
          <nav aria-label="当前项目角色与参考图">
          <CollapsibleRoot open={createOpen} onOpenChange={setCreateOpen} className="people-role-list">
          <div className="people-role-list-heading">
            <h3 className="text-sm font-medium">项目角色</h3>
            <CollapsibleTrigger asChild>
              <UiButton type="button" disabled={locked} variant="ghost" size="sm" aria-label={createOpen ? '收起新建角色' : '新建角色'}>
                {createOpen ? <ChevronDown className="people-role-arrow" aria-hidden="true" data-icon="inline-start" /> : <Plus aria-hidden="true" data-icon="inline-start" />}
                {createOpen ? '收起' : '新建'}
              </UiButton>
            </CollapsibleTrigger>
          </div>
          <CollapsibleContent className="people-role-create">
            <form onSubmit={create}>
              <FieldGroup className="gap-3">
              <Field>
                <FieldLabel htmlFor={createNameId}>新角色名称</FieldLabel>
                <Input id={createNameId} ref={nameInput} value={name} disabled={locked} aria-label="新目标角色名称" placeholder="可留空" onChange={event => setName(event.target.value)} />
              </Field>
              <UiButton type="submit" disabled={Boolean(creating) || (busy)} aria-busy={(creating) || undefined}>{(creating) && <Spinner data-icon="inline-start" aria-hidden="true" />}新建角色</UiButton>
              </FieldGroup>
            </form>
          </CollapsibleContent>
            {roles.map(item => {
              const image = item.images?.[0];
              const selected = role?.id === item.id;
              return (
                <UiButton type="button" className="people-role-option h-auto whitespace-normal text-start" key={item.id} aria-pressed={selected} disabled={locked} onClick={() => setSelectedId(item.id)} variant={selected ? 'secondary' : 'ghost'}>
                  {image
                    ? <img src={image.thumbnail_url || assetUrl(image)} alt="" loading="lazy" />
                    : <span className="people-role-option-image"><Image aria-hidden="true" data-icon="inline-start" /></span>}
                  <span className="people-role-option-copy">
                    <span className="text-sm font-medium">{item.name || '未命名角色'}</span>
                    <span className="text-xs text-muted-foreground people-role-option-meta">
                      {item.images?.length || 0} 张参考图
                    </span>
                  </span>
                  {draftRoleIds.includes(item.id) && <Badge variant="warning">未保存</Badge>}
                </UiButton>
              );
            })}
          </CollapsibleRoot>
          </nav>
          <section ref={currentRole} tabIndex={-1} className="people-role-current" aria-label="当前角色资料与参考图">
            {role
              ? <fieldset className="people-role-editor-fieldset" disabled={locked}>
                  <TargetRole key={role.id} role={role} project={project} bound={bound}
                    onPatch={onPatch} onUpload={onUpload} onImport={onImport} busy={locked} />
                </fieldset>
              : <p className="text-sm text-muted-foreground">新建角色后可编辑资料和参考图。</p>}
          </section>
        </div>
        </div>
        <footer className="people-role-footer">
          {onSeries && <UiButton type="button" disabled={locked} onClick={openSeries} variant="ghost">剧集与角色库</UiButton>}
          <div className="people-role-actions">
            <DialogClose asChild>
              <UiButton type="button" disabled={locked} variant="outline">返回人物编辑</UiButton>
            </DialogClose>
            {onChooseRole && <UiButton type="button" disabled={locked || !role} onClick={choose}>为当前人物选择此角色</UiButton>}
          </div>
        </footer>
      </DialogContent>
    </Dialog>
  );
}
