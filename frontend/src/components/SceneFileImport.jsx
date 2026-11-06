import {Collapsible as CollapsibleRoot,CollapsibleTrigger,CollapsibleContent} from "./ui/collapsible";
import React, { useEffect, useId, useRef, useState } from 'react';
import { Button as UiButton } from './ui/button';
import { Spinner } from './ui/spinner';

import { Input } from './ui/input';
import { Alert, AlertDescription } from './ui/alert';


import { AlertCircle, ChevronDown, FolderInput, Upload, X } from 'lucide-react';
import './SceneFileImport.css';

export default function SceneFileImport({
  label, kindLabel, accept, multiple = false, disabled = false, onFiles, onImport, children,
  pathLabel = '通过本机文件路径导入', placeholder = '例如：/Users/你的名字/Movies/视频.mp4',
  uploadVariant = 'outline', size = 'lg',
}) {
  const input = useRef(null);
  const pathTrigger = useRef(null);
  const restorePathFocus = useRef(false);
  const pathId = useId();
  const [open, setOpen] = useState(false);
  const [path, setPath] = useState('');
  const [uploading, setUploading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [pathError, setPathError] = useState('');
  const locked = disabled || uploading || importing;

  useEffect(() => {
    if (!open && !locked && restorePathFocus.current) {
      restorePathFocus.current = false;
      pathTrigger.current?.focus({ preventScroll: true });
    }
  }, [locked, open]);

  async function chooseFiles(event) {
    const field = event.currentTarget;
    const files = Array.from(field.files);
    if (!files.length) return;
    setUploading(true);
    try {
      await onFiles(files);
    } finally {
      field.value = '';
      setUploading(false);
    }
  }

  function closePath() {
    restorePathFocus.current = true;
    setOpen(false);
  }

  async function importPath(event) {
    event.preventDefault();
    const value = path.trim();
    if (!value || locked) return;
    setImporting(true);
    setPathError('');
    try {
      if (await onImport(value) === null) {
        setPathError('文件未能导入。请查看错误说明，修正路径后重试。');
        return;
      }
      setPath('');
      closePath();
    } catch (error) {
      setPathError(error?.message || '文件未能导入，请检查路径后重试。');
    } finally {
      setImporting(false);
    }
  }

  return (
    <CollapsibleRoot className="scene-file-controls" open={open} onOpenChange={setOpen}>
      <div className="scene-file-control-row">
        <input ref={input} type="file" hidden accept={accept} multiple={multiple}
          disabled={locked} onChange={chooseFiles} />
        <UiButton type="button" onClick={() => input.current?.click()} variant={uploadVariant} size={size} disabled={Boolean(uploading) || (disabled || importing)} aria-busy={(uploading) || undefined}>{(uploading) && <Spinner data-icon="inline-start" aria-hidden="true" />}
          <Upload aria-hidden="true" data-icon="inline-start" />{label}
        </UiButton>
        <CollapsibleTrigger asChild>
          <UiButton ref={pathTrigger} type="button" disabled={locked} variant={"outline"} size={size}>
            <FolderInput aria-hidden="true" data-icon="inline-start" />{pathLabel}
            <ChevronDown className="scene-file-path-arrow" aria-hidden="true" data-icon="inline-start" />
          </UiButton>
        </CollapsibleTrigger>
        {children}
      </div>
      <CollapsibleContent className="scene-file-path-content">
        <form className="scene-file-path-form" onSubmit={importPath}>
          <div className="scene-file-path-field">
            <label htmlFor={pathId} className="text-sm font-medium">{kindLabel}绝对路径</label>
            <Input id={pathId} placeholder={placeholder} value={path} onChange={event => { setPath(event.target.value); setPathError(''); }} disabled={locked} required aria-describedby={`${pathId}-hint`} />
          </div>
          <UiButton type="submit" size={size} disabled={Boolean(importing) || (disabled || !path.trim())} aria-busy={(importing) || undefined}>{(importing) && <Spinner data-icon="inline-start" aria-hidden="true" />}导入此文件</UiButton>
          <UiButton type="button" aria-label="取消导入" disabled={locked} onClick={closePath} variant={"outline"} size={"icon"}>
            <X aria-hidden="true" data-icon="inline-start" />
          </UiButton>
        </form>
        <span id={`${pathId}-hint`} className="text-sm text-muted-foreground">
          原文件留在原处。导入后请保留文件及其位置。
        </span>
        {pathError && <Alert role="alert" data-tone={"destructive"} variant="destructive">
          <AlertCircle size={16} aria-hidden="true" />
          <AlertDescription>{pathError}</AlertDescription>
        </Alert>}
      </CollapsibleContent>
    </CollapsibleRoot>
  );
}
