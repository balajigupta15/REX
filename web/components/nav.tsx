"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { AssetSearch } from "@/components/asset-search";

const items = [
  ["/", "Home"], ["/ai", "Agent"], ["/build", "Build"], ["/results", "Results"], ["/refine", "Refine"], ["/lab", "Lab"], ["/paper", "Tracker"],
  ["/journal", "Journal"], ["/check", "Pre-Trade Check"], ["/scout", "Scout"], ["/research", "Research"], ["/memory", "Memory"], ["/crypto", "Crypto"], ["/data", "Data"],
];
export function Nav() {
  const path = usePathname();
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-ink">
      <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-2">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <span className="grid h-6 w-6 place-items-center rounded border border-line text-xs font-semibold text-text">R</span>
          <span>Rex</span><span className="hidden text-xs font-normal text-mute sm:inline">coach with a memory</span>
        </Link>
        <nav className="flex flex-1 gap-1 overflow-x-auto text-sm">
          {items.map(([href, label]) => (
            <Link key={href} href={href} className={cn("whitespace-nowrap rounded-md px-2.5 py-1 transition", (href === "/" ? path === "/" : path.startsWith(href)) ? "bg-panel2 text-text" : "text-mute hover:text-text")}>{label}</Link>
          ))}
        </nav>
        <AssetSearch />
      </div>
    </header>
  );
}
export const Footer = () => (
  <footer className="mt-16 border-t border-line py-6 text-center text-xs text-mute">
    Educational research on historical data. Paper only. Not investment advice. Past performance does not predict future results.
    <div className="mt-1">Rex is a strategy and memory layer. It is not a broker and places no orders.</div>
  </footer>
);
