import {
  type ChangeEvent,
  type DragEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { api } from "@/api";
import type { TranscriptInfo, TranscriptSegment } from "@/types";

const MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v3"] as const;

const LANGUAGES: { code: string; label: string }[] = [
  { code: "", label: "Auto-detect" },
  { code: "en", label: "English" },
  { code: "es", label: "Spanish" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
  { code: "it", label: "Italian" },
  { code: "pt", label: "Portuguese" },
  { code: "nl", label: "Dutch" },
  { code: "ru", label: "Russian" },
  { code: "ja", label: "Japanese" },
  { code: "zh", label: "Chinese" },
  { code: "ko", label: "Korean" },
  { code: "hi", label: "Hindi" },
  { code: "ar", label: "Arabic" },
];

export function TranscribePage() {
  const [file, setFile] = useState<File | null>(null);
  const [model, setModel] = useState<string>("");
  const [language, setLanguage] = useState<string>("");
  const [segments, setSegments] = useState<TranscriptSegment[]>([]);
  const [info, setInfo] = useState<TranscriptInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

  const fileRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const segsBoxRef = useRef<HTMLOListElement>(null);

  useEffect(() => {
    if (!file) {
      setAudioUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setAudioUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => {
    if (!segsBoxRef.current) return;
    segsBoxRef.current.scrollTop = segsBoxRef.current.scrollHeight;
  }, [segments.length]);

  const text = useMemo(
    () => segments.map((s) => s.text).filter(Boolean).join(" ").trim(),
    [segments],
  );

  const handleFile = (f: File) => {
    setFile(f);
    setSegments([]);
    setInfo(null);
    setErr(null);
    setCopied(false);
  };

  const onPick = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) handleFile(f);
    e.target.value = "";
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  };

  const handleRun = useCallback(async () => {
    if (!file) return;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    setBusy(true);
    setErr(null);
    setSegments([]);
    setInfo(null);

    try {
      await api.transcribeStream(
        file,
        {
          language: language || undefined,
          model: model || undefined,
        },
        {
          signal: ac.signal,
          onInfo: setInfo,
          onSegment: (seg) => setSegments((prev) => [...prev, seg]),
          onError: (m) => setErr(m),
        },
      );
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        // user cancelled — ignore
      } else {
        setErr(e instanceof Error ? e.message : "Failed to transcribe");
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }, [file, language, model]);

  const handleCancel = () => {
    abortRef.current?.abort();
  };

  const baseName = file ? file.name.replace(/\.[^.]+$/, "") || "transcript" : "transcript";

  const copyText = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setErr("Clipboard write failed");
    }
  };

  const downloadBlob = (data: string, ext: string) => {
    const blob = new Blob([data], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${baseName}.${ext}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const downloadTxt = () => downloadBlob(text + "\n", "txt");
  const downloadSrt = () => downloadBlob(buildSrt(segments), "srt");

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="border-b border-line bg-surface px-4 py-2.5">
        <div className="mx-auto flex w-full max-w-3xl flex-wrap items-center gap-2">
          <span className="mr-auto text-sm font-semibold text-zinc-200">Transcribe</span>
          <label className="flex items-center gap-1.5 text-xs text-muted">
            Model
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              disabled={busy}
              className="rounded-lg border border-line bg-bg px-2 py-1 text-xs text-zinc-100 focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-40"
            >
              <option value="">default</option>
              {MODEL_SIZES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1.5 text-xs text-muted">
            Language
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              disabled={busy}
              className="rounded-lg border border-line bg-bg px-2 py-1 text-xs text-zinc-100 focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-40"
            >
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-6">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            onClick={() => fileRef.current?.click()}
            className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition ${
              dragOver
                ? "border-accent bg-panel/60"
                : "border-line bg-panel/30 hover:border-muted"
            }`}
          >
            <svg
              className="h-10 w-10 text-muted"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={1.5}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9 8.25V18a3 3 0 0 0 3 3h0a3 3 0 0 0 3-3V5.25a2.25 2.25 0 0 0-4.5 0V15a1.5 1.5 0 0 0 3 0V6"
              />
            </svg>
            <p className="text-sm text-zinc-200">
              {file ? file.name : "Click to choose, or drop an audio file here"}
            </p>
            <p className="text-xs text-muted">
              .wav · .mp3 · .m4a · .ogg · .webm — non-WAV needs ffmpeg on PATH
            </p>
            <input
              ref={fileRef}
              type="file"
              accept="audio/*,video/*"
              onChange={onPick}
              className="hidden"
            />
          </div>

          {audioUrl && (
            <audio
              controls
              src={audioUrl}
              className="w-full rounded-xl bg-panel/40"
            />
          )}

          <div className="flex items-center justify-end gap-2">
            {busy ? (
              <button
                onClick={handleCancel}
                className="rounded-xl bg-danger px-4 py-2 text-sm font-semibold text-white"
              >
                Cancel
              </button>
            ) : (
              <button
                onClick={handleRun}
                disabled={!file}
                className="rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-bg transition hover:bg-accent/80 disabled:opacity-40"
              >
                Transcribe
              </button>
            )}
          </div>

          {(info || busy) && (
            <div className="flex flex-wrap items-center gap-3 rounded-xl bg-panel/40 px-3 py-2 text-xs text-muted">
              {info && (
                <>
                  <span>
                    language:{" "}
                    <span className="text-zinc-200">
                      {info.language}
                      {info.language_probability > 0 &&
                        ` (${Math.round(info.language_probability * 100)}%)`}
                    </span>
                  </span>
                  <span>
                    duration:{" "}
                    <span className="text-zinc-200">{info.duration.toFixed(1)}s</span>
                  </span>
                </>
              )}
              <span>
                segments: <span className="text-zinc-200">{segments.length}</span>
              </span>
              {busy && <span className="text-accent">transcribing…</span>}
            </div>
          )}

          {err && (
            <div className="rounded-xl border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
              {err}
            </div>
          )}

          {segments.length > 0 && (
            <>
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
                  Transcript
                </h3>
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={copyText}
                    className="rounded-lg bg-panel px-2.5 py-1 text-xs text-zinc-200 hover:bg-line"
                  >
                    {copied ? "Copied!" : "Copy text"}
                  </button>
                  <button
                    onClick={downloadTxt}
                    className="rounded-lg bg-panel px-2.5 py-1 text-xs text-zinc-200 hover:bg-line"
                  >
                    Download .txt
                  </button>
                  <button
                    onClick={downloadSrt}
                    className="rounded-lg bg-panel px-2.5 py-1 text-xs text-zinc-200 hover:bg-line"
                  >
                    Download .srt
                  </button>
                </div>
              </div>
              <ol
                ref={segsBoxRef}
                className="flex max-h-[60vh] flex-col gap-1.5 overflow-y-auto pr-1"
              >
                {segments.map((s) => (
                  <li
                    key={s.index}
                    className="flex gap-3 rounded-lg bg-panel/40 px-3 py-2 text-sm"
                  >
                    <span className="shrink-0 font-mono text-xs text-muted">
                      {fmtTs(s.start)}
                    </span>
                    <span className="text-zinc-100">{s.text}</span>
                  </li>
                ))}
              </ol>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function fmtTs(seconds: number, srt = false): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.floor((seconds * 1000) % 1000);
  const sep = srt ? "," : ".";
  const pad = (n: number, w = 2) => n.toString().padStart(w, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}${sep}${pad(ms, 3)}`;
}

function buildSrt(segments: TranscriptSegment[]): string {
  return segments
    .map(
      (s, i) =>
        `${i + 1}\n${fmtTs(s.start, true)} --> ${fmtTs(s.end, true)}\n${s.text}\n`,
    )
    .join("\n");
}
