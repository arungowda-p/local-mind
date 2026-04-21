import type {
  AssistantApp,
  AssistantCommandResponse,
  AssistantStatus,
  CodeResult,
  HealthStatus,
  KnowledgeStats,
  ModelInfo,
  TranscribeOptions,
  TranscribeStreamCallbacks,
  TranscriptSegment,
} from "./types";

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
    fd.append("audio", blob, blobFilename(blob));
    const params = language ? `?language=${encodeURIComponent(language)}` : "";
    const res = await fetch(`${BASE}/api/voice/transcribe${params}`, {
      method: "POST",
      body: fd,
    });
    if (!res.ok) throw new Error(await res.text());
    const data = (await res.json()) as { text: string };
    return data.text;
  },

  transcribeStream: async (
    blob: Blob | File,
    opts: TranscribeOptions = {},
    cb: TranscribeStreamCallbacks = {},
  ): Promise<TranscriptSegment[]> => {
    const fd = new FormData();
    const name = blob instanceof File ? blob.name : blobFilename(blob);
    fd.append("audio", blob, name);
    const params = new URLSearchParams();
    if (opts.language) params.set("language", opts.language);
    if (opts.model) params.set("model", opts.model);
    const qs = params.toString() ? `?${params.toString()}` : "";

    const res = await fetch(`${BASE}/api/voice/transcribe/stream${qs}`, {
      method: "POST",
      body: fd,
      signal: cb.signal,
    });
    if (!res.ok || !res.body) {
      throw new Error((await res.text().catch(() => "")) || `${res.status} ${res.statusText}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    const segments: TranscriptSegment[] = [];
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const event = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        for (const line of event.split("\n")) {
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trimStart();
          if (!payload) continue;
          try {
            const data = JSON.parse(payload);
            if (data.type === "info") {
              cb.onInfo?.(data);
            } else if (data.type === "segment") {
              segments.push(data);
              cb.onSegment?.(data);
            } else if (data.type === "error") {
              cb.onError?.(data.message ?? "Transcription failed");
            } else if (data.type === "done") {
              cb.onDone?.(data.text ?? segments.map((s) => s.text).join(" ").trim());
            }
          } catch {
            // ignore malformed events
          }
        }
      }
    }
    return segments;
  },

  assistantStatus: () => json<AssistantStatus>("/api/assistant/status"),
  assistantStart: () =>
    json<AssistantStatus>("/api/assistant/start", { method: "POST" }),
  assistantStop: () =>
    json<AssistantStatus>("/api/assistant/stop", { method: "POST" }),
  assistantTrigger: () =>
    json<{ status: string }>("/api/assistant/trigger", { method: "POST" }),
  assistantCommand: (text: string, speak = true) =>
    json<AssistantCommandResponse>("/api/assistant/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, speak }),
    }),
  assistantApps: (limit = 200) =>
    json<AssistantApp[]>(`/api/assistant/apps?limit=${limit}`),
  assistantAppsRefresh: () =>
    json<{ total: number }>("/api/assistant/apps/refresh", { method: "POST" }),
};

function blobFilename(blob: Blob): string {
  const subtype = (blob.type.split("/")[1] ?? "").split(";")[0]?.trim();
  const ext = subtype && subtype.length <= 8 ? subtype : "wav";
  return `recording.${ext}`;
}
