"use client";
import { Suspense, useEffect, useState } from "react";
import { PageHead } from "@/components/page-head";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Alert } from "@/components/ui/misc";
import { PriceTicker } from "@/components/ai/ticker";
import { AgentPanel } from "@/components/ai/agent-panel";
import { Builder } from "@/components/ai/builder";
import { useSearchParams, useRouter } from "next/navigation";
import { aiGet, errText, type Status } from "@/components/ai/lib";

function AiPage() {
  const query = useSearchParams(); const router = useRouter();
  const [status, setStatus] = useState<Status | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { aiGet<Status>("/status").then(setStatus).catch((e) => setErr(errText(e))); }, []);
  return (
    <div className="space-y-4">
      <PageHead title="Strategy research agent" sub="Describe a goal. The agent writes its own strategy code, tests it on real data with a locked final period, argues both sides, and reports what the evidence says." />
      <PriceTicker symbol="BTCUSDT" />
      {err && <Alert title="Could not reach the strategy service">{err}</Alert>}
      <Tabs value={query.get("tab") === "chat" || query.has("chat") ? "chat" : "agent"} onValueChange={(v) => router.replace(v === "chat" ? `/ai?tab=chat${query.get("chat") ? `&chat=${query.get("chat")}` : ""}` : "/ai")}>
        <TabsList><TabsTrigger value="agent">Research agent</TabsTrigger><TabsTrigger value="chat">Chat builder</TabsTrigger></TabsList>
        <TabsContent value="agent"><Suspense fallback={null}><AgentPanel status={status} /></Suspense></TabsContent>
        <TabsContent value="chat"><Builder status={status} /></TabsContent>
      </Tabs>
    </div>
  );
}

export default function AiRoute() { return <Suspense fallback={<p>Loading research…</p>}><AiPage /></Suspense>; }
