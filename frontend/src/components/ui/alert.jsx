import * as React from "react"
import { cva } from "class-variance-authority";
import { cn } from "@/lib/utils"

const alertVariants = cva(
  "relative grid w-full grid-cols-[0_1fr] items-start gap-y-0.5 rounded-lg border px-4 py-3 text-sm has-[>svg]:grid-cols-[calc(var(--spacing)*4)_1fr] has-[>svg]:gap-x-3 [&>svg]:size-4 [&>svg]:translate-y-0.5 [&>svg]:text-current",
  {
    variants: {
      variant: {
        default: "bg-card text-card-foreground",
        destructive:
          "bg-card text-destructive *:data-[slot=alert-description]:text-destructive/90 [&>svg]:text-current",
      },
      tone: {
        info: "border-[var(--info-border)] bg-[var(--info-muted)] text-info *:data-[slot=alert-description]:text-current",
        warning: "border-[var(--warning-border)] bg-[var(--warning-muted)] text-warning *:data-[slot=alert-description]:text-current",
        success: "border-[var(--success-border)] bg-[var(--success-muted)] text-success *:data-[slot=alert-description]:text-current",
        destructive: "border-[var(--destructive-border)] bg-[var(--destructive-muted)] text-destructive *:data-[slot=alert-description]:text-current",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Alert({
  className,
  variant,
  tone,
  'data-tone': dataTone,
  ...props
}) {
  return (
    <div
      data-slot="alert"
      data-tone={tone || dataTone}
      role="alert"
      className={cn(alertVariants({ variant, tone: tone || dataTone }), className)}
      {...props}
    />
  )
}

function AlertTitle({
  className,
  ...props
}) {
  return (
    <div
      data-slot="alert-title"
      className={cn(
        "col-start-2 line-clamp-1 min-h-4 font-medium tracking-tight",
        className
      )}
      {...props}
    />
  )
}

function AlertDescription({
  className,
  ...props
}) {
  return (
    <div
      data-slot="alert-description"
      className={cn(
        "col-start-2 grid justify-items-start gap-1 text-sm text-muted-foreground [&_p]:leading-relaxed",
        className
      )}
      {...props}
    />
  )
}

export { Alert, AlertTitle, AlertDescription }
