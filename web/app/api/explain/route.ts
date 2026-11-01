import { NextResponse } from "next/server";
import { createHash } from "crypto";

// Optional LLM "Explain" (Groq or Gemini). Cached in memory by input hash. The app is fully functional without keys.
const cache = new Map<string, string>();
const enabled = () => !!(process.env.GROQ_API_KEY || process.env.GEMINI_API_KEY);

export async function GET() {
  return NextResponse.json({ enabled: enabled(), provider: process.env.GROQ_API_KEY ? "groq" : process.env.GEMINI_API_KEY ? "gemini" : null });
}

export async function POST(req: Request) {
  if (!enabled()) return NextResponse.json({ error: "No GROQ_API_KEY or GEMINI_API_KEY set" }, { status: 501 });
  const { kind, facts } = await req.json();
  const body = JSON.stringify({ kind, facts });
  if (body.length > 20000) return NextResponse.json({ error: "payload too large" }, { status: 413 });
  const key = createHash("sha1").update(body).digest("hex");
  if (cache.has(key)) return NextResponse.json({ text: cache.get(key), cached: true });
  const prompt = `You are Rex, a calm trading coach. Explain the following ${kind} facts to a retail trader in 4 short sentences of plain English. ` +
    `Use ONLY the numbers given; never invent figures; do not give investment advice or tell them to buy or sell; mention the main risk or caveat. Facts JSON:\n${JSON.stringify(facts)}`;
  try {
    let text = "";
    if (process.env.GROQ_API_KEY) {
      const r = await fetch("https://api.groq.com/openai/v1/chat/completions", {
        method: "POST", headers: { "content-type": "application/json", authorization: `Bearer ${process.env.GROQ_API_KEY}` },
        body: JSON.stringify({ model: process.env.GROQ_MODEL || "llama-3.3-70b-versatile", temperature: 0.2, messages: [{ role: "user", content: prompt }] }),
      });
      const j = await r.json(); if (!r.ok) throw new Error(j?.error?.message || "groq error"); text = j.choices?.[0]?.message?.content || "";
    } else {
      const m = process.env.GEMINI_MODEL || "gemini-3.6-flash";
      const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${m}:generateContent?key=${process.env.GEMINI_API_KEY}`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }], generationConfig: { temperature: 0.2 } }),
      });
      const j = await r.json(); if (!r.ok) throw new Error(j?.error?.message || "gemini error"); text = j.candidates?.[0]?.content?.parts?.[0]?.text || "";
    }
    if (!text) throw new Error("empty response");
    cache.set(key, text);
    return NextResponse.json({ text, cached: false });
  } catch (e: any) {
    return NextResponse.json({ error: e.message || "explain failed" }, { status: 502 });
  }
}
