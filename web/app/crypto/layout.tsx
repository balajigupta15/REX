"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Bitcoin } from "lucide-react";
import { CryptoProvider } from "@/lib/crypto";
import { PriceStrip, NO_ORDERS } from "@/components/crypto-bits";
import { cn } from "@/lib/utils";

const tabs = [["/crypto", "Crypto Build"], ["/crypto/results", "Results"], ["/crypto/refine", "Refine"], ["/crypto/tracker", "Crypto Tracker"], ["/crypto/memory", "Last time"]];

export default function CryptoLayout({ children }: { children: ReactNode }) {
  const path = usePathname();
  return (
    <CryptoProvider>
      <div data-testid="crypto-section">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <span className="flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1 text-sm font-semibold text-text"><Bitcoin className="h-4 w-4" />Crypto</span>
          <nav className="flex gap-1 overflow-x-auto text-sm" data-testid="crypto-subnav">
            {tabs.map(([href, label]) => (
              <Link key={href} href={href} className={cn("whitespace-nowrap rounded-md px-3 py-1.5 transition", (href === "/crypto" ? path === "/crypto" : path.startsWith(href)) ? "bg-panel2 text-text" : "text-mute hover:text-text")}>{label}</Link>))}
          </nav>
          <span className="ml-auto text-xs text-mute">BTC · ETH · SOL (USDT) · separate from the NSE stock section</span>
        </div>
        <PriceStrip />
        {children}
        <p className="mt-8 rounded-md border border-line bg-panel p-3 text-xs text-mute" data-testid="crypto-disclaimer">{NO_ORDERS} Crypto is highly volatile and trades 24/7. Educational research on historical data, not investment advice.</p>
      </div>
    </CryptoProvider>
  );
}
