import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const variants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-text/40",
  {
    variants: {
      variant: {
        default: "bg-rex text-black hover:bg-white",
        outline: "border border-line bg-panel hover:bg-panel2 text-text",
        ghost: "hover:bg-panel2 text-mute hover:text-text",
        danger: "bg-loss/15 text-loss border border-loss/40 hover:bg-loss/25",
      },
      size: { default: "h-9 px-3.5", sm: "h-8 px-3 text-xs", lg: "h-10 px-5 text-sm" },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof variants> { asChild?: boolean }
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild, ...p }, ref) => {
  const C = asChild ? Slot : "button";
  return <C ref={ref as any} className={cn(variants({ variant, size }), className)} {...p} />;
});
Button.displayName = "Button";
