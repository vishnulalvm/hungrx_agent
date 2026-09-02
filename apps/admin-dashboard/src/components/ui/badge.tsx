import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold leading-none",
  {
    variants: {
      variant: {
        default: "border-transparent bg-bg-soft text-ink-soft",
        ok: "border-transparent bg-ok-soft text-ok",
        attn: "border-transparent bg-attn-soft text-attn",
        accent: "border-transparent bg-accent-soft text-accent",
        outline: "border-line-strong text-ink-soft",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
