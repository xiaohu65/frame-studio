// Translate existing workflow status names into the design system's semantic roles.
export function feedbackVariant(color) {
  return ({ red: 'destructive', amber: 'warning', yellow: 'warning', green: 'success',
    blue: 'info', indigo: 'info', gray: 'secondary' })[color] || 'secondary';
}

export function feedbackTone(color) {
  return ({ red: 'destructive', amber: 'warning', yellow: 'warning', green: 'success',
    blue: 'info', indigo: 'info', gray: 'info' })[color] || 'info';
}

export function textTone(color) {
  return color === 'gray' ? 'muted' : feedbackTone(color);
}

export function textToneClass(tone) {
  return ({ muted: 'text-muted-foreground', destructive: 'text-destructive',
    warning: 'text-warning', success: 'text-success', info: 'text-info',
    gray: 'text-muted-foreground', red: 'text-destructive', amber: 'text-warning' })[tone] || 'text-inherit';
}
