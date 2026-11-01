import * as React from "react";
import { AlertTriangle, Info } from "lucide-react";
import { cn } from "@/lib/utils";

export const Skeleton = ({ className }: { className?: string }) => <div className={cn("animate-pulse rounded-md bg-panel2", className)} />;

export function Alert({ tone = "loss", title, children, className }: { tone?: "loss" | "warn" | "info" | "gain"; title?: string; children?: React.ReactNode; className?: string }) {
  const c = { loss: "border-loss/40 bg-loss/10 text-loss", warn: "border-warn/40 bg-warn/10 text-warn", info: "border-sky/40 bg-sky/10 text-sky", gain: "border-gain/40 bg-gain/10 text-gain" }[tone];
  const Icon = tone === "info" ? Info : AlertTriangle;
  return (
    <div role="alert" className={cn("flex gap-3 rounded-md border p-3 text-sm", c, className)}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0" />
      <div>{title && <div className="font-semibold">{title}</div>}<div className="text-text/90">{children}</div></div>
    </div>
  );
}

export const Table = ({ className, ...p }: React.TableHTMLAttributes<HTMLTableElement>) => (
  <div className="overflow-x-auto"><table className={cn("w-full text-sm", className)} {...p} /></div>
);
export const Th = ({ className, ...p }: React.ThHTMLAttributes<HTMLTableCellElement>) => <th className={cn("whitespace-nowrap border-b border-line px-3 py-1.5 text-left text-xs font-medium text-mute", className)} {...p} />;
export const Td = ({ className, ...p }: React.TdHTMLAttributes<HTMLTableCellElement>) => <td className={cn("whitespace-nowrap border-b border-line/60 px-3 py-1.5", className)} {...p} />;
