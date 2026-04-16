import { useCallback, useEffect, useState } from "react";
import { api } from "@/api";
import type { ModelInfo, KnowledgeStats } from "@/types";

interface SidebarProps {
  open: boolean;
  onClose: () => void;
}

export function Sidebar({ open, onClose }: SidebarProps) {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [stats, setStats] = useState<KnowledgeStats | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [learnUrl, setLearnUrl] = useState("");
  const [learnBusy, setLearnBusy] = useState(false);
  const [learnMsg, setLearnMsg] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [m, s] = await Promise.all([api.listModels(), api.knowledgeStats()]);
      setModels(m);
      setStats(s);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    if (open) refresh();
  }, [open, refresh]);

  const handleLoad = async (name: string) => {
    setLoading(name);
    try {
      await api.loadModel(name);
      await refresh();
    } catch {
      // ignore
    } finally {
      setLoading(null);
    }
  };

  const handleUnload = async () => {
    await api.unloadModel();
    refresh();
  };

  const handleLearn = async () => {
    if (!learnUrl.trim()) return;
    setLearnBusy(true);
    setLearnMsg("");
    try {
      const res = await api.learnUrl(learnUrl.trim());
      setLearnMsg(res.status === "learned" ? `Learned ${res.chunks} chunks` : res.reason ?? "Skipped");
      setLearnUrl("");
      refresh();
    } catch (e: unknown) {
      setLearnMsg(e instanceof Error ? e.message : "Failed");
    } finally {
      setLearnBusy(false);
    }
  };

  const handleClear = async () => {
    await api.clearKnowledge();
    refresh();
  };

  return (
    <>
      {open && (
        <div className="fixed inset-0 z-30 bg-black/50 lg:hidden" onClick={onClose} />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-80 flex-col gap-6 overflow-y-auto border-r border-line bg-surface p-5 transition-transform duration-200 lg:static lg:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-zinc-100">LocalMind</h2>
          <button onClick={onClose} className="text-muted hover:text-zinc-200 lg:hidden">&times;</button>
        </div>

        {/* Models */}
        <section>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Models</h3>
          <ul className="space-y-1.5">
            {models.map((m) => (
              <li key={m.name} className="flex items-center justify-between rounded-lg bg-panel px-3 py-2 text-sm">
                <div>
                  <span className="font-medium text-zinc-200">{m.name}</span>
                  {m.size_mb != null && <span className="ml-1 text-xs text-muted">({m.size_mb} MB)</span>}
                  {m.loaded && <span className="ml-2 text-xs text-ok">active</span>}
                </div>
                {m.loaded ? (
                  <button onClick={handleUnload} className="text-xs text-danger hover:underline">Unload</button>
                ) : m.downloaded ? (
                  <button
                    onClick={() => handleLoad(m.name)}
                    disabled={loading !== null}
                    className="text-xs text-accent hover:underline disabled:opacity-40"
                  >
                    {loading === m.name ? "Loading…" : "Load"}
                  </button>
                ) : (
                  <button
                    onClick={() => handleLoad(m.name)}
                    disabled={loading !== null}
                    className="text-xs text-warn hover:underline disabled:opacity-40"
                  >
                    {loading === m.name ? "Downloading…" : "Download"}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>

        {/* Learn */}
        <section>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Learn from URL</h3>
          <div className="flex gap-2">
            <input
              value={learnUrl}
              onChange={(e) => setLearnUrl(e.target.value)}
              placeholder="https://…"
              className="flex-1 rounded-lg border border-line bg-bg px-2.5 py-1.5 text-sm text-zinc-100 placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <button
              onClick={handleLearn}
              disabled={learnBusy}
              className="rounded-lg bg-accent px-3 py-1.5 text-sm font-semibold text-bg disabled:opacity-40"
            >
              {learnBusy ? "…" : "Add"}
            </button>
          </div>
          {learnMsg && <p className="mt-1.5 text-xs text-muted">{learnMsg}</p>}
        </section>

        {/* Knowledge */}
        <section>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Knowledge base</h3>
          <p className="text-sm text-zinc-300">
            {stats ? `${stats.documents} document chunks` : "—"}
          </p>
          <button
            onClick={handleClear}
            className="mt-1.5 text-xs text-danger hover:underline"
          >
            Clear all knowledge
          </button>
        </section>
      </aside>
    </>
  );
}
