import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
const v = cva("inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium", {
  variants: {
    tone: {
      neutral: "border-line bg-panel2 text-mute",
      gain: "border-gain/40 bg-gain/10 text-gain",
      loss: "border-loss/40 bg-loss/10 text-loss",
      warn: "border-warn/40 bg-warn/10 text-warn",
      sky: "border-sky/40 bg-sky/10 text-sky",
      rex: "border-line bg-panel2 text-text",
    },
  },
  defaultVariants: { tone: "neutral" },
});
export const Badge = ({ className, tone, ...p }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof v>) => <span className={cn(v({ tone }), className)} {...p} />;
