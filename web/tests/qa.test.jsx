import React from "react";
import { afterEach, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { FormField, Input, Label, Select } from "../components/ui/input";
import { completedResult } from "../components/ai/agents";
import { api } from "../lib/api";

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; });

test("fields connect each label to a distinct input and select", () => {
  const html = renderToStaticMarkup(<><FormField><Label>Stock</Label><Input /></FormField><FormField><Label>Side</Label><Select><option>Buy</option></Select></FormField></>);
  const ids = [...html.matchAll(/<(?:input|select)[^>]*id="([^"]+)"/g)].map((m) => m[1]);
  const labels = [...html.matchAll(/<label[^>]*for="([^"]+)"/g)].map((m) => m[1]);
  expect(ids.length).toBe(2); expect(new Set(ids).size).toBe(2); expect(labels).toEqual(ids);
});

test("explicit field associations are preserved", () => {
  const html = renderToStaticMarkup(<FormField><Label htmlFor="stock">Stock</Label><Input id="stock" /></FormField>);
  expect(html).toContain('for="stock"'); expect(html).toContain('id="stock"');
});

test("failed API runs persist an error outcome on the agent card", () => {
  const run = { status: "failed", final: null, error: { code: "data_failed", message: "Data unavailable", retryable: true } };
  expect(completedResult(run)).toMatchObject({ status: "error", headline: "Data unavailable", versionId: null });
  expect(completedResult({ ...run, status: "running" })).toBeNull();
});

test("API reports structured field validation in readable form", async () => {
  globalThis.fetch = (async () => new Response(JSON.stringify({ detail: [{ loc: ["body", "trade", "qty"], msg: "Input should be greater than 0" }] }), { status: 422 }));
  await expect(api("/crypto/tracker/start")).rejects.toThrow("trade.qty: Input should be greater than 0");
});

test("stalled requests abort with an actionable timeout", async () => {
  globalThis.fetch = ((_url, init) => new Promise((_resolve, reject) => init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true })));
  await expect(api("/health", { timeoutMs: 1 })).rejects.toThrow("request timed out");
});

test("caller cancellation reaches fetch without becoming a connectivity error", async () => {
  globalThis.fetch = ((_url, init) => new Promise((_resolve, reject) => {
    if (init?.signal?.aborted) reject(new DOMException("Aborted", "AbortError"));
    else init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
  }));
  const controller = new AbortController();
  const pending = api("/health", { signal: controller.signal }); controller.abort();
  await expect(pending).rejects.toThrow("Aborted");
});
