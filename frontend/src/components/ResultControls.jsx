import React from 'react';
import { Alert, AlertDescription } from './ui/alert';


import { CircleAlert, Info } from 'lucide-react';

export { GenerationDisclosure as ResultDisclosure, GenerationSelect as ResultSelect } from './GenerationControls';

export function ResultNotice({ children, tone = 'info', role, className }) {
  const resolvedTone = tone ?? color ?? 'info';
  const Icon = resolvedTone === 'destructive' || resolvedTone === 'warning' ? CircleAlert : Info;

  return (
    <Alert role={role} className={className} data-tone={resolvedTone} variant={resolvedTone === 'destructive' ? 'destructive' : 'default'}>
      <Icon size={17} aria-hidden="true" />
      <AlertDescription className="result-notice-copy">{children}</AlertDescription>
    </Alert>
  );
}
