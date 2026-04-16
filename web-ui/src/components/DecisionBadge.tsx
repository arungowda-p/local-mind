import type { DecisionMeta } from "@/types";

const intentColors: Record<string, string> = {
  question: "bg-blue-500/20 text-blue-300",
  action: "bg-violet-500/20 text-violet-300",
  learn: "bg-emerald-500/20 text-emerald-300",
  chitchat: "bg-amber-500/20 text-amber-300",
  clarify: "bg-rose-500/20 text-rose-300",
  code: "bg-cyan-500/20 text-cyan-300",
};

const actionLabels: Record<string, string> = {
  rag_chat: "KB answer",
  direct_chat: "General",
  learn_url: "Learn URL",
  summarize: "Summarize",
  clarify: "Clarify",
  write_code: "Code Gen",
  run_code: "Run Code",
};

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    pct >= 75 ? "bg-emerald-400" : pct >= 45 ? "bg-amber-400" : "bg-rose-400";
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1.5 w-14 rounded-full bg-line">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[0.65rem] tabular-nums text-muted">{pct}%</span>
    </div>
  );
}

interface DecisionBadgeProps {
  decision: DecisionMeta;
}

export function DecisionBadge({ decision }: DecisionBadgeProps) {
  const intent = decision.intent.intent;
  const action = decision.action.action;
  const kbConf = decision.confidence.confidence;

  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[0.68rem]">
      <span className={`rounded-full px-2 py-0.5 font-medium ${intentColors[intent] ?? "bg-line text-muted"}`}>
        {intent}
      </span>
      <span className="rounded-full bg-panel px-2 py-0.5 font-medium text-zinc-400">
        {actionLabels[action] ?? action}
      </span>
      {decision.confidence.has_context && (
        <ConfidenceBar value={kbConf} />
      )}
    </div>
  );
}
