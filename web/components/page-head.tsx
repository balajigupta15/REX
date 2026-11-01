export const PageHead = ({ title, sub, right }: { title: string; sub?: string; right?: React.ReactNode }) => (
  <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
    <div><h1 className="text-xl font-semibold tracking-tight">{title}</h1>{sub && <p className="mt-1 max-w-2xl text-sm text-mute">{sub}</p>}</div>
    {right}
  </div>
);
