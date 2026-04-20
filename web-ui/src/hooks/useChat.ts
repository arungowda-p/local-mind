import { useCallback, useRef, useState } from "react";
import type { ChatMessage, CodeResult, DecisionMeta, WebSearchSummary } from "@/types";

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (text: string, useRag: boolean, temperature: number) => {
      const userMsg: ChatMessage = { role: "user", content: text };
      setMessages((prev) => [...prev, userMsg]);
      setStreaming(true);

      const history = messages.map((m) => ({ role: m.role, content: m.content }));
      const controller = new AbortController();
      abortRef.current = controller;

      const assistantMsg: ChatMessage = { role: "assistant", content: "" };
      setMessages((prev) => [...prev, assistantMsg]);

      let pendingDecision: DecisionMeta | undefined;

      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: text,
            history,
            use_rag: useRag,
            temperature,
            stream: true,
            smart: true,
          }),
          signal: controller.signal,
        });

        if (!res.ok) {
          const err = await res.text();
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            return [...prev.slice(0, -1), { ...last, content: `Error: ${err}` }];
          });
          setStreaming(false);
          return;
        }

        const reader = res.body?.getReader();
        const decoder = new TextDecoder();
        if (!reader) throw new Error("No response body");

        let buffer = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const payload = line.slice(6).trim();
            if (payload === "[DONE]") {
              // Attach final decision to message
              if (pendingDecision) {
                setMessages((prev) => {
                  const last = prev[prev.length - 1];
                  return [...prev.slice(0, -1), { ...last, decision: pendingDecision }];
                });
              }
              break;
            }
            try {
              const parsed = JSON.parse(payload) as {
                token?: string;
                error?: string;
                decision?: DecisionMeta;
                code_results?: CodeResult[];
                web_sources?: WebSearchSummary;
                rewrite?: string;
              };
              if (parsed.code_results) {
                setMessages((prev) => {
                  const last = prev[prev.length - 1];
                  return [...prev.slice(0, -1), { ...last, codeResults: parsed.code_results }];
                });
              }
              if (parsed.web_sources) {
                setMessages((prev) => {
                  const last = prev[prev.length - 1];
                  return [...prev.slice(0, -1), { ...last, webSources: parsed.web_sources }];
                });
              }
              if (parsed.rewrite) {
                setMessages((prev) => {
                  const last = prev[prev.length - 1];
                  return [...prev.slice(0, -1), { ...last, content: parsed.rewrite as string }];
                });
              }
              if (parsed.decision) {
                pendingDecision = parsed.decision;
                // Attach immediately so it appears while streaming
                setMessages((prev) => {
                  const last = prev[prev.length - 1];
                  return [...prev.slice(0, -1), { ...last, decision: parsed.decision }];
                });
              }
              if (parsed.error) {
                setMessages((prev) => {
                  const last = prev[prev.length - 1];
                  return [
                    ...prev.slice(0, -1),
                    { ...last, content: last.content + `\n\nError: ${parsed.error}` },
                  ];
                });
                break;
              }
              if (parsed.token) {
                setMessages((prev) => {
                  const last = prev[prev.length - 1];
                  return [...prev.slice(0, -1), { ...last, content: last.content + parsed.token }];
                });
              }
            } catch {
              // skip parse errors
            }
          }
        }
      } catch (e: unknown) {
        if (e instanceof DOMException && e.name === "AbortError") {
          // user cancelled
        } else {
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            return [...prev.slice(0, -1), { ...last, content: last.content + `\n\nConnection error.` }];
          });
        }
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [messages],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const clear = useCallback(() => {
    setMessages([]);
  }, []);

  return { messages, streaming, send, stop, clear };
}
