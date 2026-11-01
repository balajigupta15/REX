import type { Metadata } from "next";
import "./globals.css";
import { Nav, Footer } from "@/components/nav";
import { Providers } from "@/lib/store";

export const metadata: Metadata = { title: "Rex: the trading coach with a memory", description: "Test, refine and remember your trades. Educational, paper only." };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <Nav />
          <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
          <Footer />
        </Providers>
      </body>
    </html>
  );
}
