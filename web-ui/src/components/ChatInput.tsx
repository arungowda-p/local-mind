import { type FormEvent, useState } from "react";
import { VoiceButton } from "@/components/VoiceButton";

interface ChatInputProps {
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
  voiceRecording: boolean;
  voiceTranscribing: boolean;
  onVoiceToggle: () => void;
  disabled?: boolean;
}

export function ChatInput({
  onSend,
  onStop,
  streaming,
  voiceRecording,
  voiceTranscribing,
  onVoiceToggle,
  disabled,
}: ChatInputProps) {
  const [text, setText] = useState("");

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (streaming) {
      onStop();
      return;
    }
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setText("");
  };

  return (
    <form onSubmit={handleSubmit} className="border-t border-line bg-surface px-4 py-3">
      <div className="mx-auto flex max-w-3xl items-center gap-2">
        <VoiceButton
          recording={voiceRecording}
          transcribing={voiceTranscribing}
          onClick={onVoiceToggle}
        />
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={disabled ? "Load a model to start" : "Type a message…"}
          disabled={disabled}
          className="flex-1 rounded-xl border border-line bg-bg px-4 py-2.5 text-sm text-zinc-100 placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-accent disabled:opacity-40"
        />
        <button
          type="submit"
          disabled={disabled && !streaming}
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-sm font-semibold transition ${
            streaming
              ? "bg-danger text-white"
              : "bg-accent text-bg hover:bg-accent/80 disabled:opacity-40"
          }`}
        >
          {streaming ? (
            <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 16 16">
              <rect width="10" height="10" x="3" y="3" rx="1" />
            </svg>
          ) : (
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 12 3.269 3.125A59.769 59.769 0 0 1 21.485 12 59.768 59.768 0 0 1 3.27 20.875L5.999 12Zm0 0h7.5" />
            </svg>
          )}
        </button>
      </div>
    </form>
  );
}
