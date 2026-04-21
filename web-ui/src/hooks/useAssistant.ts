import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/api";
import type {
  AssistantActionResult,
  AssistantEvent,
  AssistantStatus,
} from "@/types";

export interface LogEntry {
  id: number;
  ts: number;
  kind: "you" | "assistant" | "action" | "info" | "error" | "wake";
  text: string;
  intent?: string;
  ok?: boolean;
}

export interface UseAssistant {
  status: AssistantStatus | null;
  log: LogEntry[];
  level: number;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  trigger: () => Promise<void>;
  runCommand: (text: string, speak?: boolean) => Promise<void>;
  refresh: () => Promise<void>;
  rescanApps: () => Promise<void>;
  clearLog: () => void;
}

const MAX_LOG = 200;

export function useAssistant(): UseAssistant {
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [level, setLevel] = useState(0);
  const logIdRef = useRef(0);
  const esRef = useRef<EventSource | null>(null);

  const pushLog = useCallback((entry: Omit<LogEntry, "id" | "ts"> & { ts?: number }) => {
    setLog((prev) => {
      const next: LogEntry = {
        id: ++logIdRef.current,
        ts: entry.ts ?? Date.now() / 1000,
        kind: entry.kind,
        text: entry.text,
        intent: entry.intent,
        ok: entry.ok,
      };
      const out = [...prev, next];
      return out.length > MAX_LOG ? out.slice(out.length - MAX_LOG) : out;
    });
  }, []);

  const refresh = useCallback(async () => {
    try {
      const s = await api.assistantStatus();
      setStatus(s);
    } catch {
      // ignore
    }
  }, []);

  const start = useCallback(async () => {
    const s = await api.assistantStart();
    setStatus(s);
  }, []);

  const stop = useCallback(async () => {
    const s = await api.assistantStop();
    setStatus(s);
    setLevel(0);
  }, []);

  const trigger = useCallback(async () => {
    await api.assistantTrigger().catch(() => {});
  }, []);

  const runCommand = useCallback(
    async (text: string, speak = true) => {
      pushLog({ kind: "you", text });
      try {
        const res = await api.assistantCommand(text, speak);
        pushLog({
          kind: "action",
          text: res.speech || res.detail || "(no response)",
          intent: res.intent,
          ok: res.ok,
        });
      } catch (e) {
        pushLog({ kind: "error", text: (e as Error).message });
      }
    },
    [pushLog],
  );

  const rescanApps = useCallback(async () => {
    try {
      const res = await api.assistantAppsRefresh();
      pushLog({ kind: "info", text: `Rescanned apps — ${res.total} known.` });
      setStatus((prev) => (prev ? { ...prev, app_count: res.total } : prev));
    } catch (e) {
      pushLog({ kind: "error", text: `Rescan failed: ${(e as Error).message}` });
    }
  }, [pushLog]);

  const clearLog = useCallback(() => setLog([]), []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const es = new EventSource("/api/assistant/events");
    esRef.current = es;
    es.onmessage = (ev) => {
      try {
        const evt = JSON.parse(ev.data) as AssistantEvent;
        handleEvent(evt);
      } catch {
        // ignore parse errors
      }
    };
    es.onerror = () => {
      // browser will auto-reconnect
    };
    return () => {
      es.close();
      esRef.current = null;
    };

    function handleEvent(evt: AssistantEvent) {
      switch (evt.kind) {
        case "state": {
          const st = evt.state as AssistantStatus["state"] | undefined;
          if (st) {
            setStatus((prev) =>
              prev
                ? { ...prev, state: st, running: st !== "stopped" }
                : prev,
            );
          }
          break;
        }
        case "level":
          setLevel(Number(evt.rms ?? 0));
          break;
        case "wake":
          pushLog({
            kind: "wake",
            text: `Wake: ${String(evt.model ?? "")} (${Number(evt.score ?? 0).toFixed(2)})`,
          });
          break;
        case "transcript":
          pushLog({ kind: "you", text: String(evt.text ?? "") });
          break;
        case "action": {
          const result = (evt.result ?? {}) as AssistantActionResult;
          pushLog({
            kind: "action",
            text: result.speech || result.detail || "(no response)",
            intent: String(evt.intent ?? ""),
            ok: result.ok,
          });
          break;
        }
        case "info":
          pushLog({ kind: "info", text: String(evt.message ?? "") });
          break;
        case "apps": {
          const count = Number(evt.count ?? 0);
          setStatus((prev) => (prev ? { ...prev, app_count: count } : prev));
          break;
        }
        case "error":
          pushLog({ kind: "error", text: String(evt.message ?? "error") });
          break;
      }
    }
  }, [pushLog]);

  return {
    status,
    log,
    level,
    start,
    stop,
    trigger,
    runCommand,
    refresh,
    rescanApps,
    clearLog,
  };
}
