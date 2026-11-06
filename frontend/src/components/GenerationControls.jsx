import React, { useId } from 'react';
import { Button as UiButton } from './ui/button';

import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectItem } from './ui/select';

import { Collapsible, CollapsibleTrigger, CollapsibleContent } from './ui/collapsible';
import { ChevronDown } from 'lucide-react';

export function GenerationDisclosure({
  label, children, className, id, defaultOpen, disabled, triggerClassName, contentClassName,
}) {
  return (
    <Collapsible id={id} tabIndex={id ? -1 : undefined} defaultOpen={defaultOpen} disabled={disabled}
      className={['generation-disclosure group/disclosure', className].filter(Boolean).join(' ')} data-generation-disclosure="">
      <CollapsibleTrigger asChild>
        <UiButton type="button" data-generation-toggle="" className={['generation-disclosure-trigger h-auto w-full justify-between whitespace-normal py-2 text-start', triggerClassName].filter(Boolean).join(' ')} variant={"ghost"} size={"lg"}>
          <span className="generation-disclosure-label">{label}</span><ChevronDown aria-hidden="true" data-icon="inline-start" className="transition-transform group-data-[state=open]/disclosure:rotate-180" />
        </UiButton>
      </CollapsibleTrigger>
      <CollapsibleContent forceMount data-generation-content=""
        className={['generation-disclosure-content', contentClassName].filter(Boolean).join(' ')}>
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}

export function GenerationSelect({ label, ariaLabel, value, onValueChange, disabled, options, className, placeholder }) {
  const id = useId();
  const selected = options.find(([key]) => key === value);

  return (
    <div className={['generation-field', className].filter(Boolean).join(' ')}>
      <label htmlFor={id} className="text-sm font-medium">{label}</label>
      <Select value={value} onValueChange={onValueChange} disabled={disabled}>
        <SelectTrigger id={id} aria-label={ariaLabel}><SelectValue placeholder={placeholder}>{selected?.[1]}</SelectValue></SelectTrigger>
        <SelectContent position="popper"><SelectGroup>
          {options.map(([key, text]) => <SelectItem key={key} value={key}>{text}</SelectItem>)}
        </SelectGroup></SelectContent>
      </Select>
    </div>
  );
}

export function revealGenerationDisclosures(element) {
  const toggles = [];
  for (let parent = element; parent; parent = parent.parentElement) {
    if (parent.matches('[data-generation-disclosure][data-state="closed"], [data-slot="collapsible"][data-state="closed"]')) {
      const toggle = parent.querySelector(':scope > [data-generation-toggle], :scope > [data-slot="collapsible-trigger"]');
      if (toggle && !toggle.disabled) toggles.push(toggle);
    }
  }
  toggles.reverse().forEach(toggle => toggle.click());
  return toggles.length > 0;
}
