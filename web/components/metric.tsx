import { cn } from "@/lib/utils";
export function Metric({ label, value, sub, className, valueClass }: { label: string; value: React.ReactNode; sub?: React.ReactNode; className?: string; valueClass?: string }) {
  return (
    <div className={cn("rounded-md border border-line bg-panel p-3", className)}>
      <div className="text-xs text-mute">{label}</div>
      <div className={cn("num mt-0.5 text-xl font-semibold", valueClass)}>{value}</div>
      {sub && <div className="mt-1 text-xs text-mute">{sub}</div>}
    </div>
  );
}
