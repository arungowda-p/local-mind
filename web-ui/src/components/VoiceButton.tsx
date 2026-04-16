interface VoiceButtonProps {
  recording: boolean;
  transcribing: boolean;
  onClick: () => void;
}

export function VoiceButton({ recording, transcribing, onClick }: VoiceButtonProps) {
  const label = transcribing ? "Transcribing…" : recording ? "Stop recording" : "Voice input";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={transcribing}
      title={label}
      className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition ${
        recording
          ? "animate-pulse bg-danger text-white"
          : "bg-panel text-muted hover:bg-line hover:text-zinc-200"
      } disabled:opacity-40`}
    >
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 0 0 6-6v-1.5m-6 7.5a6 6 0 0 1-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 0 1-3-3V4.5a3 3 0 1 1 6 0v8.25a3 3 0 0 1-3 3Z" />
      </svg>
    </button>
  );
}
