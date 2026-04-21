import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useAssistant, type LogEntry } from "@/hooks/useAssistant";
import type { AssistantState } from "@/types";

const STATE_LABEL: Record<AssistantState, string> = {
  stopped: "Offline",
  starting: "Starting…",
  idle: "Listening for wake word",
  listening: "Listening",
  processing: "Thinking",
  speaking: "Speaking",
  error: "Error",
};

const STATE_COLOR: Record<AssistantState, string> = {
  stopped: "#475569",
  starting: "#6b7280",
  idle: "#6c8cff",
  listening: "#22d3ee",
  processing: "#a78bfa",
  speaking: "#fbbf24",
  error: "#f87171",
};

const EXAMPLES = [
  "open youtube",
  "open gmail",
  "open github",
  "watch lofi beats on youtube",
  "find rust lifetimes on stack overflow",
  "create a document called meeting notes",
  "create a pdf called invoice",
  "write a document about transformers",
  "open notepad",
  "what time is it",
  "volume up",
  "next track",
  "battery",
  "brightness 60",
  "take a screenshot",
  "refresh apps",
  "lock the computer",
];

export function AssistantPage() {
  const { status, log, level, start, stop, trigger, runCommand, rescanApps, clearLog } =
    useAssistant();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [speak, setSpeak] = useState(true);
  const logRef = useRef<HTMLOListElement>(null);

  const state: AssistantState = status?.state ?? "stopped";
  const running = !!status?.running;

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log.length]);

  const handleToggle = async () => {
    setBusy(true);
    setErr(null);
    try {
      if (running) await stop();
      else await start();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;
    setInput("");
    await runCommand(text, speak);
  };

  const orbScale = useMemo(() => {
    const base = state === "listening" ? 1 + level * 0.35 : 1;
    return base;
  }, [level, state]);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="border-b border-line bg-surface px-4 py-2.5">
        <div className="mx-auto flex w-full max-w-4xl flex-wrap items-center gap-3 text-xs text-muted">
          <span className="text-sm font-semibold text-zinc-200">Assistant</span>
          <span
            className="flex items-center gap-1.5 rounded-full bg-panel px-2.5 py-0.5 text-xs font-medium"
            style={{ color: STATE_COLOR[state] }}
          >
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: STATE_COLOR[state] }}
            />
            {STATE_LABEL[state]}
          </span>
          {status && (
            <>
              <span>wake: <span className="text-zinc-200">{status.wake_word}</span></span>
              <span>hotkey: <span className="text-zinc-200">{status.hotkey}</span></span>
              <span>apps: <span className="text-zinc-200">{status.app_count}</span></span>
              {!status.wake_available && (
                <span className="text-warn">wake word unavailable (hotkey only)</span>
              )}
              {!status.tts_available && (
                <span className="text-warn">no TTS — text only</span>
              )}
            </>
          )}
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={() => rescanApps()}
              className="rounded-lg bg-panel px-2.5 py-1 text-xs text-zinc-200 hover:bg-line"
              title="Rescan Start Menu for newly installed apps"
            >
              Rescan apps
            </button>
            {running && (
              <button
                onClick={() => trigger()}
                disabled={!running}
                className="rounded-lg bg-panel px-2.5 py-1 text-xs text-zinc-200 hover:bg-line disabled:opacity-40"
              >
                Trigger (no wake)
              </button>
            )}
            <button
              onClick={handleToggle}
              disabled={busy}
              className={`rounded-lg px-3 py-1 text-xs font-semibold transition ${
                running
                  ? "bg-danger text-white hover:bg-danger/80"
                  : "bg-accent text-bg hover:bg-accent/80"
              } disabled:opacity-40`}
            >
              {busy ? "…" : running ? "Stop" : "Start"}
            </button>
          </div>
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-6 overflow-y-auto px-4 py-6">
        <div className="mx-auto flex w-full max-w-4xl flex-col items-center gap-4">
          <Orb state={state} scale={orbScale} />
          <p className="text-center text-sm text-muted">
            {running
              ? state === "idle"
                ? `Say "hey jarvis" or press ${status?.hotkey ?? "the hotkey"}`
                : STATE_LABEL[state]
              : "Click Start to begin listening."}
          </p>
        </div>

        <div className="mx-auto w-full max-w-4xl">
          <form onSubmit={handleSubmit} className="flex items-center gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Type a command (e.g. open calculator)…"
              className="flex-1 rounded-xl border border-line bg-bg px-4 py-2.5 text-sm text-zinc-100 placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-accent"
            />
            <label className="flex items-center gap-1 text-xs text-muted">
              <input
                type="checkbox"
                checked={speak}
                onChange={(e) => setSpeak(e.target.checked)}
                className="accent-accent"
              />
              speak
            </label>
            <button
              type="submit"
              className="rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-bg hover:bg-accent/80"
            >
              Run
            </button>
          </form>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                onClick={() => runCommand(ex, speak)}
                className="rounded-full bg-panel/60 px-2.5 py-0.5 text-xs text-muted hover:bg-panel hover:text-zinc-200"
              >
                {ex}
              </button>
            ))}
          </div>
          {err && (
            <div className="mt-3 rounded-xl border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
              {err}
            </div>
          )}
        </div>

        <div className="mx-auto w-full max-w-4xl">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
              Conversation
            </h3>
            {log.length > 0 && (
              <button
                onClick={clearLog}
                className="text-xs text-muted hover:text-zinc-200"
              >
                clear
              </button>
            )}
          </div>
          <ol
            ref={logRef}
            className="flex max-h-[55vh] flex-col gap-1.5 overflow-y-auto pr-1"
          >
            {log.length === 0 ? (
              <li className="rounded-xl bg-panel/30 px-3 py-4 text-center text-xs text-muted">
                No activity yet. Try saying "hey jarvis, open notepad".
              </li>
            ) : (
              log.map((entry) => <LogRow key={entry.id} entry={entry} />)
            )}
          </ol>
        </div>
      </div>
    </div>
  );
}

function LogRow({ entry }: { entry: LogEntry }) {
  const { kind } = entry;
  const style =
    kind === "you"
      ? "bg-accent/10 border-accent/30 text-zinc-100"
      : kind === "action"
        ? entry.ok === false
          ? "bg-danger/10 border-danger/30 text-danger"
          : "bg-panel/60 border-line text-zinc-100"
        : kind === "wake"
          ? "bg-panel/40 border-line text-[color:var(--color-accent,#22d3ee)]"
          : kind === "error"
            ? "bg-danger/10 border-danger/30 text-danger"
            : "bg-panel/30 border-line text-muted";

  const label: Record<LogEntry["kind"], string> = {
    you: "you",
    assistant: "jarvis",
    action: entry.intent ? `→ ${entry.intent}` : "→",
    info: "info",
    error: "error",
    wake: "wake",
  };

  return (
    <li className={`flex gap-3 rounded-xl border px-3 py-2 text-sm ${style}`}>
      <span className="shrink-0 font-mono text-[10px] uppercase tracking-wider opacity-70">
        {label[kind]}
      </span>
      <span className="flex-1 whitespace-pre-wrap break-words">{entry.text}</span>
    </li>
  );
}

function Orb({ state, scale }: { state: AssistantState; scale: number }) {
  const color = STATE_COLOR[state];
  const pulse = state === "idle" || state === "listening";
  const shimmer = state === "processing" || state === "speaking";

  return (
    <div
      className="relative flex h-56 w-56 items-center justify-center"
      style={{ transform: `scale(${scale.toFixed(3)})`, transition: "transform 120ms linear" }}
    >
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className={`absolute inset-0 rounded-full border ${pulse ? "animate-orb-ring" : ""}`}
          style={{
            borderColor: color,
            opacity: 0.25 - i * 0.07,
            animationDelay: `${i * 0.45}s`,
          }}
        />
      ))}
      <span
        className={`absolute h-32 w-32 rounded-full ${shimmer ? "animate-orb-spin" : ""}`}
        style={{
          background: `radial-gradient(circle at 30% 30%, ${color}66, transparent 65%),
                       radial-gradient(circle at 70% 70%, #ffffff22, transparent 60%),
                       radial-gradient(circle, ${color}, ${color}22 70%)`,
          boxShadow: `0 0 60px 10px ${color}55, inset 0 0 40px ${color}66`,
        }}
      />
      <span
        className="absolute h-16 w-16 rounded-full bg-zinc-100"
        style={{ opacity: 0.08 }}
      />
    </div>
  );
}
