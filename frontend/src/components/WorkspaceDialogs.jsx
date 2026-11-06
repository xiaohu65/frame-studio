import React, { useId, useRef } from 'react';
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Field, FieldLabel, FieldDescription } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Dialog, DialogContent, DialogTitle, DialogDescription, DialogClose } from "@/components/ui/dialog";
import { AlertCircle, X } from 'lucide-react';

function restoreFocus(returnFocus) {
  const target = returnFocus && 'current' in returnFocus ? returnFocus.current : returnFocus;
  if (target?.isConnected) target.focus({ preventScroll: true });
}

export function WorkspaceSupport({ title, onClose, returnFocus, children, className, closeSize }) {
  const closeButton = useRef(null);

  return (
    <Dialog open onOpenChange={open => { if (!open) onClose(); }}>
      <DialogContent className={['support-dialog workspace-support-dialog', className].filter(Boolean).join(' ')} aria-describedby={undefined} onOpenAutoFocus={event => {
          event.preventDefault();
          closeButton.current?.focus();
        }} onCloseAutoFocus={event => {
          event.preventDefault();
          restoreFocus(returnFocus);
        }} showCloseButton={false} style={{ maxWidth: "none" }}>
        <div className="support-dialog-inner">
          <div className="support-dialog-heading flex items-center justify-between gap-4">
            <DialogTitle>{title}</DialogTitle>
            <DialogClose asChild>
              <Button ref={closeButton} type="button" aria-label={`关闭${title}`} variant="ghost" size="icon">
                <X size={20} aria-hidden="true" />
              </Button>
            </DialogClose>
          </div>
          <div className="support-dialog-body">{children}</div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function NewProjectDialog({ name, onNameChange, onClose, onSubmit, busy, error, returnFocus }) {
  const nameId = useId();
  const nameInput = useRef(null);
  const dismissed = useRef(false);

  return (
    <Dialog open onOpenChange={open => {
      if (!open) {
        dismissed.current = true;
        onClose();
      }
    }}>
      <DialogContent className="new-project-dialog" onOpenAutoFocus={event => {
          event.preventDefault();
          nameInput.current?.focus();
        }} onCloseAutoFocus={event => {
          event.preventDefault();
          // A successful submission moves focus to the new workspace heading.
          if (dismissed.current) restoreFocus(returnFocus);
        }} showCloseButton={false} style={{ maxWidth: "490px" }}>
        
          <form onSubmit={event => {
            if (busy) event.preventDefault();
            else onSubmit(event);
          }} className="flex flex-col gap-4">
            <div className="new-project-dialog-heading flex items-center justify-between gap-4">
              <DialogTitle>新建视频项目</DialogTitle>
              <DialogClose asChild>
                <Button type="button" aria-label="关闭新建项目" variant="ghost" size="icon">
                  <X size={18} aria-hidden="true" />
                </Button>
              </DialogClose>
            </div>
            {error && (
              <Alert role="alert" variant="destructive">
                <AlertCircle size={17} aria-hidden="true" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <DialogDescription>
              一个项目用于处理一条原视频。创建后，先导入原片，再指定需要替换的人物和场景。
            </DialogDescription>
            <Field>
              <FieldLabel htmlFor={nameId}>项目名称</FieldLabel>
              <Input ref={nameInput} id={nameId} value={name} onChange={event => onNameChange(event.target.value)} placeholder="例如：第 1 集人物替换" aria-describedby={`${nameId}-hint`} />
              <FieldDescription id={`${nameId}-hint`}>
                留空将使用“未命名项目”。也可独立制作视频，无需建立剧集。
              </FieldDescription>
            </Field>
            <p className="text-sm text-muted-foreground">
              原片材料、人物对应和处理记录保存在本机。第 4 步确认材料与本次费用后，才会上传并调用生成 API。
            </p>
            <div className="flex justify-end">
              <Button type="submit" disabled={(busy)} aria-busy={(busy) || undefined}>{(busy) && <Spinner />}创建项目，开始导入原片</Button>
            </div>
          </form>
        
      </DialogContent>
    </Dialog>
  );
}
