import type { CodeResult, HealthStatus, KnowledgeStats, ModelInfo } from "./types";

const BASE = "";

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, init);
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(body || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => json<HealthStatus>("/api/health"),

  listModels: () => json<ModelInfo[]>("/api/models"),

  loadModel: (name: string) =>
    json<{ status: string; model: string }>("/api/models/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),

  unloadModel: () =>
    json<{ status: string }>("/api/models/unload", { method: "POST" }),

  learnUrl: (url: string) =>
    json<{ url: string; status: string; chunks?: number; reason?: string }>("/api/learn/url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    }),

  learnText: (text: string, source?: string) =>
    json<{ source: string; status: string; chunks?: number }>("/api/learn/text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, source: source ?? "paste" }),
    }),

  knowledgeStats: () => json<KnowledgeStats>("/api/knowledge/stats"),

  clearKnowledge: () =>
    json<{ status: string }>("/api/knowledge/clear", { method: "POST" }),

  runCode: (code: string, language: string, timeout = 30, stdin?: string) =>
    json<CodeResult>("/api/code/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, language, timeout, stdin: stdin || null }),
    }),

  formatCode: (code: string, language: string) =>
    json<{ code: string }>("/api/code/format", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, language }),
    }),

  runtimes: () => json<Record<string, string | null>>("/api/code/runtimes"),

  transcribe: async (blob: Blob, language?: string): Promise<string> => {
    const fd = new FormData();
    fd.append("audio", blob, "recording.wav");
    const params = language ? `?language=${language}` : "";
    const res = await fetch(`${BASE}/api/voice/transcribe${params}`, {
      method: "POST",
      body: fd,
    });
    if (!res.ok) throw new Error(await res.text());
    const data = (await res.json()) as { text: string };
    return data.text;
  },
};
