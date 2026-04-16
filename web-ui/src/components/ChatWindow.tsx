import { useEffect, useRef } from "react";
import { ChatBubble } from "@/components/ChatBubble";
import type { ChatMessage } from "@/types";

interface ChatWindowProps {
  messages: ChatMessage[];
}

export function ChatWindow({ messages }: ChatWindowProps) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6">
      {messages.length === 0 ? (
        <div className="flex h-full items-center justify-center">
          <div className="max-w-md text-center">
            <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-panel">
              <svg className="h-8 w-8 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 18v-5.25m0 0a6.01 6.01 0 0 0 1.5-.189m-1.5.189a6.01 6.01 0 0 1-1.5-.189m3.75 7.478a12.06 12.06 0 0 1-4.5 0m3.75 2.383a14.406 14.406 0 0 1-3 0M14.25 18v-.192c0-.983.658-1.823 1.508-2.316a7.5 7.5 0 1 0-7.517 0c.85.493 1.509 1.333 1.509 2.316V18" />
              </svg>
            </div>
            <h2 className="text-lg font-semibold text-zinc-200">LocalMind</h2>
            <p className="mt-1 text-sm text-muted">
              Load a model from the sidebar, then start chatting. Teach it new knowledge with URLs.
            </p>
          </div>
        </div>
      ) : (
        <div className="mx-auto flex max-w-3xl flex-col gap-3">
          {messages.map((m, i) => (
            <ChatBubble key={i} message={m} />
          ))}
          <div ref={endRef} />
        </div>
      )}
    </div>
  );
}
