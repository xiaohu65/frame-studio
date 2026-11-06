import React, { useId } from 'react';
import { Input } from "@/components/ui/input";
import { Field, FieldLabel, FieldDescription } from "@/components/ui/field";
import { GenerationSelect } from './GenerationControls';

export { GenerationDisclosure as SeriesDisclosure } from './GenerationControls';
export { ResultNotice as SeriesNotice } from './ResultControls';

const OPTION_PREFIX = 'series-option:';

export function SeriesSelect({ value, options, onValueChange, ...props }) {
  const selected = String(value ?? '');
  const exists = options.some(([key]) => String(key) === selected);

  return (
    <GenerationSelect {...props} value={exists ? OPTION_PREFIX + selected : ''} options={options.map(([key, label]) => [OPTION_PREFIX + key, label])} onValueChange={option => onValueChange(option.slice(OPTION_PREFIX.length))} />
  );
}

export function SeriesField({ label, ariaLabel, hint, className, ...props }) {
  const id = useId();

  return (
    <Field className={['generation-field', 'series-field', className].filter(Boolean).join(' ')}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input id={id} aria-label={ariaLabel} aria-describedby={hint ? id + '-hint' : undefined} {...props} />
      {hint && <FieldDescription id={id + '-hint'}>{hint}</FieldDescription>}
    </Field>
  );
}
