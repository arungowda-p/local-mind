export type View = "chat" | "transcribe" | "assistant";

interface TopBarProps {
  modelName: string | null;
  view: View;
  onViewChange: (v: View) => void;
  onMenuClick: () => void;
  onClearChat: () => void;
}

export function TopBar({
  modelName,
  view,
  onViewChange,
  onMenuClick,
  onClearChat,
}: TopBarProps) {
  return (
    <header className="flex items-center justify-between border-b border-line bg-surface px-4 py-2.5">
      <div className="flex items-center gap-3">
        <button
          onClick={onMenuClick}
          className="rounded-lg p-1.5 text-muted hover:bg-panel hover:text-zinc-200 lg:hidden"
        >
          <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
          </svg>
        </button>

        <div className="flex items-center gap-1 rounded-lg bg-panel/60 p-0.5 text-xs font-medium">
          <ViewTab active={view === "chat"} onClick={() => onViewChange("chat")}>
            Chat
          </ViewTab>
          <ViewTab active={view === "transcribe"} onClick={() => onViewChange("transcribe")}>
            Transcribe
          </ViewTab>
          <ViewTab active={view === "assistant"} onClick={() => onViewChange("assistant")}>
            Assistant
          </ViewTab>
        </div>

        {view === "chat" && modelName && (
          <span className="rounded-full bg-panel px-2.5 py-0.5 text-xs font-medium text-accent">
            {modelName}
          </span>
        )}
      </div>

      {view === "chat" && (
        <button
          onClick={onClearChat}
          className="rounded-lg px-2.5 py-1 text-xs font-medium text-muted hover:bg-panel hover:text-zinc-200"
        >
          New chat
        </button>
      )}
    </header>
  );
}

function ViewTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-md px-2.5 py-1 transition ${
        active
          ? "bg-bg text-zinc-100 shadow-sm"
          : "text-muted hover:text-zinc-200"
      }`}
    >
      {children}
    </button>
  );
}
