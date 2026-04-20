import { useCallback, useEffect, useState } from "react";
import { api } from "@/api";
import type { CodeResult } from "@/types";

interface CodeBlockProps {
  code: string;
  language: string;
}

const RUNNABLE = new Set([
  "python", "javascript", "typescript", "shell", "bash", "powershell",
  "py", "js", "ts", "sh",
]);

const BROWSER_ONLY_RE =
  /\b(prompt|alert|confirm|document|window|localStorage|sessionStorage)\s*[(.[]/;

function findBrowserOnlyAPIs(code: string, language: string): string[] {
  const lang = language.toLowerCase();
  if (!["js", "javascript", "ts", "typescript"].includes(lang)) return [];
  const found = new Set<string>();
  let m: RegExpExecArray | null;
  const re = new RegExp(BROWSER_ONLY_RE.source, "g");
  while ((m = re.exec(code)) !== null) {
    found.add(m[1]);
  }
  return [...found];
}

function langLabel(lang: string): string {
  const map: Record<string, string> = {
    py: "Python", python: "Python", js: "JavaScript", javascript: "JavaScript",
    ts: "TypeScript", typescript: "TypeScript", sh: "Shell", shell: "Shell",
    bash: "Bash", powershell: "PowerShell", ps1: "PowerShell",
    html: "HTML", css: "CSS", sql: "SQL", json: "JSON", yaml: "YAML",
    rust: "Rust", go: "Go", java: "Java", cpp: "C++", c: "C",
  };
  return map[lang.toLowerCase()] ?? lang;
}

export function CodeBlock({ code: rawCode, language }: CodeBlockProps) {
  const [code, setCode] = useState(rawCode);
  const [result, setResult] = useState<CodeResult | null>(null);
  const [running, setRunning] = useState(false);
  const [copied, setCopied] = useState(false);
  const [showInput, setShowInput] = useState(false);
  const [stdin, setStdin] = useState("");
  const [formatting, setFormatting] = useState(false);
  const canRun = RUNNABLE.has(language.toLowerCase());

  useEffect(() => {
    if (rawCode === code) return;
    // Auto-format when code looks messy: single-line, or has very long lines
    const lines = rawCode.split("\n");
    const needsFormat =
      (lines.length <= 3 && rawCode.length > 60) ||
      lines.some((l) => l.trimStart().length > 120);
    if (needsFormat) {
      api.formatCode(rawCode, language)
        .then((r) => {
          if (r.code.trim().length > 10) setCode(r.code);
          else setCode(rawCode);
        })
        .catch(() => setCode(rawCode));
    } else {
      setCode(rawCode);
    }
  }, [rawCode, language]);

  const handleFormat = useCallback(async () => {
    setFormatting(true);
    try {
      const r = await api.formatCode(code, language);
      setCode(r.code);
    } catch { /* keep current */ }
    finally { setFormatting(false); }
  }, [code, language]);

  const browserOnly = findBrowserOnlyAPIs(code, language);

  const handleRun = useCallback(async () => {
    if (browserOnly.length > 0) {
      setResult({
        language,
        exit_code: -1,
        stdout: "",
        stderr:
          `This code uses browser-only API${browserOnly.length > 1 ? "s" : ""}: ` +
          `${browserOnly.join(", ")}. The runner is Node.js, so these are undefined. ` +
          "Replace with a hardcoded value or read from process.stdin (use the Input button).",
        timed_out: false,
        duration_ms: 0,
        ok: false,
      });
      return;
    }
    setRunning(true);
    setResult(null);
    try {
      const r = await api.runCode(code, language, 30, stdin || undefined);
      setResult(r);
    } catch (e) {
      setResult({
        language,
        exit_code: -1,
        stdout: "",
        stderr: e instanceof Error ? e.message : "Failed to run",
        timed_out: false,
        duration_ms: 0,
        ok: false,
      });
    } finally {
      setRunning(false);
    }
  }, [code, language, stdin, browserOnly]);

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [code]);

  return (
    <div className="my-2 overflow-hidden rounded-lg border border-line">
      {/* Header bar */}
      <div className="flex items-center justify-between bg-bg px-3 py-1.5">
        <span className="text-[0.7rem] font-medium text-muted">
          {langLabel(language)}
        </span>
        <div className="flex items-center gap-1.5">
          <button
            onClick={handleFormat}
            disabled={formatting}
            className="rounded px-2 py-0.5 text-[0.65rem] font-medium text-muted hover:bg-line hover:text-zinc-200"
            title="Auto-format code"
          >
            {formatting ? "…" : "Format"}
          </button>
          <button
            onClick={handleCopy}
            className="rounded px-2 py-0.5 text-[0.65rem] font-medium text-muted hover:bg-line hover:text-zinc-200"
          >
            {copied ? "Copied" : "Copy"}
          </button>
          {canRun && (
            <>
              <button
                onClick={() => setShowInput((v) => !v)}
                className={`rounded px-2 py-0.5 text-[0.65rem] font-medium ${
                  showInput
                    ? "bg-blue-600/30 text-blue-300"
                    : "text-muted hover:bg-line hover:text-zinc-200"
                }`}
                title="Provide stdin / test input"
              >
                Input
              </button>
              <button
                onClick={handleRun}
                disabled={running}
                className="rounded bg-emerald-600/80 px-2.5 py-0.5 text-[0.65rem] font-semibold text-white hover:bg-emerald-500 disabled:opacity-40"
              >
                {running ? "Running…" : "▶ Run"}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Stdin input area */}
      {showInput && (
        <div className="border-b border-line bg-[#0d1220] px-3 py-2">
          <label className="mb-1 block text-[0.65rem] font-medium text-muted">
            stdin / test input
          </label>
          <textarea
            value={stdin}
            onChange={(e) => setStdin(e.target.value)}
            placeholder={'e.g. 121\nor: arg1 arg2'}
            rows={2}
            className="w-full resize-y rounded border border-line bg-bg px-2 py-1.5 font-mono text-[0.75rem] text-zinc-300 placeholder:text-zinc-600 focus:border-blue-500 focus:outline-none"
          />
          <p className="mt-1 text-[0.6rem] text-zinc-500">
            This text is piped to the program's standard input (stdin).
            For programs that read via input() / readline / etc.
          </p>
        </div>
      )}

      {/* Code display */}
      <pre className="overflow-x-auto bg-[#0a0e17] px-3 py-2.5 font-mono text-[0.78rem] leading-relaxed text-zinc-300">
        <code>{code}</code>
      </pre>

      {/* Execution output */}
      {result && (
        <div
          className={`border-t px-3 py-2 text-[0.75rem] ${
            result.ok
              ? "border-emerald-800/50 bg-emerald-950/30"
              : "border-red-800/50 bg-red-950/30"
          }`}
        >
          <div className="mb-1 flex items-center gap-2">
            <span
              className={`font-semibold ${
                result.ok ? "text-emerald-400" : "text-red-400"
              }`}
            >
              {result.ok
                ? "OK"
                : result.timed_out
                  ? "Timed out"
                  : `Exit ${result.exit_code}`}
            </span>
            <span className="text-muted">{result.duration_ms}ms</span>
          </div>
          {result.stdout && (
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[0.72rem] text-zinc-300">
              {result.stdout}
            </pre>
          )}
          {result.stderr && (
            <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[0.72rem] text-red-300">
              {result.stderr}
            </pre>
          )}
          {!result.stdout && !result.stderr && (
            <span className="text-muted">No output</span>
          )}
        </div>
      )}
    </div>
  );
}
