import React, { useId, useRef } from 'react';
import { Button } from "@/components/ui/button";
import { AlertDialog, AlertDialogContent, AlertDialogTitle, AlertDialogDescription, AlertDialogCancel } from "@/components/ui/alert-dialog";
import { Field, FieldLabel, FieldSet, FieldLegend } from "@/components/ui/field";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import './SourceDraftConflict.css';

const OPTIONS = [
  ['mine', '保留我的修改'],
  ['latest', '使用最新内容'],
];

function displayValue(value) {
  if (value === undefined) return '删除此记录';
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

export default function SourceDraftConflict({ conflicts, choices, onChoice, onCancel, onConfirm }) {
  const prefix = useId();
  const previousFocus = useRef(null);
  const complete = conflicts.length > 0 && conflicts.every(({ key }) => (
    choices[key] === 'mine' || choices[key] === 'latest'
  ));

  function rememberFocus(event) {
    previousFocus.current = event.currentTarget?.ownerDocument.activeElement;
  }

  function restoreFocus(event) {
    const target = previousFocus.current;
    if (!target?.isConnected || target === target.ownerDocument.body || target.matches(':disabled')) return;
    event.preventDefault();
    target.focus({ preventScroll: true });
  }

  return (
    <AlertDialog open onOpenChange={open => { if (!open) onCancel(); }}>
      <AlertDialogContent className="source-draft-conflict" onOpenAutoFocus={rememberFocus} onCloseAutoFocus={restoreFocus} style={{ maxWidth: "760px" }}>
        <header className="source-conflict-heading">
          <AlertDialogTitle>项目已更新，请选择冲突内容</AlertDialogTitle>
          <AlertDialogDescription>
            你的草稿仍保留。未冲突的修改和最新材料会一起保存；以下内容需逐项选择。
          </AlertDialogDescription>
        </header>
        <div className="source-conflict-list">
          {conflicts.map((conflict, index) => {
            const legendId = `${prefix}-${index}-legend`;
            return (
              <FieldSet key={conflict.key} className="source-conflict-field">
                <FieldLegend id={legendId}>{conflict.label || '修改内容'}</FieldLegend>
                <RadioGroup value={choices[conflict.key] || ''} aria-labelledby={legendId} onValueChange={choice => onChoice(conflict.key, choice)}>
                  {OPTIONS.map(([choice, label]) => {
                    const inputId = `${prefix}-${index}-${choice}`;
                    return (
                      <Field orientation="horizontal" key={choice} className="source-conflict-option">
                        <RadioGroupItem value={choice} id={inputId} />
                        <div className="source-conflict-value">
                          <FieldLabel htmlFor={inputId}>{label}</FieldLabel>
                          <pre tabIndex={0} aria-label={`${label}的内容`}>{displayValue(conflict[choice])}</pre>
                        </div>
                      </Field>
                    );
                  })}
                </RadioGroup>
              </FieldSet>
            );
          })}
        </div>
        <footer className="source-conflict-actions">
          <AlertDialogCancel asChild>
            <Button type="button" variant="outline">返回编辑，保留草稿</Button>
          </AlertDialogCancel>
          <Button type="button" onClick={() => { if (complete) onConfirm(choices); }} variant="default" disabled={!complete}>
            按所选内容保存
          </Button>
        </footer>
      </AlertDialogContent>
    </AlertDialog>
  );
}
