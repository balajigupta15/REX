"use client";
import { Database, Brain } from "lucide-react";
import { Badge } from "./ui/badge";
import type { MemStatus } from "@/lib/hooks";

export function BackendChip({ status }: { status: MemStatus | null }) {
  if (!status) return <Badge tone="neutral">memory: loading…</Badge>;
  const m = status.meko;
  if (m.enabled && m.connected) return <Badge tone="gain" title={`MCP endpoint ${m.endpoint}. ${m.tool_count} tools; SQLite stays the source of truth.`}><Brain className="h-3 w-3" /> Meko live · memory backend</Badge>;
  if (m.enabled && m.connected == null) return <Badge tone="warn"><Brain className="h-3 w-3" /> Meko: connecting…</Badge>;
  if (m.enabled) return <Badge tone="loss" title={m.last_error || ""}><Brain className="h-3 w-3" /> Meko unreachable · using SQLite</Badge>;
  return <Badge tone="neutral" title="Set MEKO_TOKEN to mirror memory into Meko"><Database className="h-3 w-3" /> memory: local SQLite</Badge>;
}
