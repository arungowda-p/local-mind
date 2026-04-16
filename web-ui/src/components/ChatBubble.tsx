import { CodeBlock } from "@/components/CodeBlock";
import { DecisionBadge } from "@/components/DecisionBadge";
import type { ChatMessage } from "@/types";

interface ChatBubbleProps {
  message: ChatMessage;
}

interface Segment {
  type: "text" | "code";
  content: string;
  language?: string;
}

// Handles all common LLM code-fence variants:
//   ```js\ncode\n```    (standard)
//   ```js\ncode ```     (closing on same line)
//   ```js code ```      (no newline at all)
//   ``` js\ncode\n```   (space before lang)
//   ```\ncode\n```      (no language)
const CODE_FENCE = /```\s*(\w*)\s*\n?([\s\S]*?)```/g;

function parseContent(text: string): Segment[] {
  const segments: Segment[] = [];
  let lastIndex = 0;

  for (const match of text.matchAll(CODE_FENCE)) {
    const before = text.slice(lastIndex, match.index);
    if (before.trim()) segments.push({ type: "text", content: before });

    const raw = match[2];
    const code = raw.trim();
    if (code) {
      segments.push({
        type: "code",
        content: code,
        language: match[1]?.trim() || "text",
      });
    }
    lastIndex = (match.index ?? 0) + match[0].length;
  }

  const rest = text.slice(lastIndex);
  if (rest.trim()) segments.push({ type: "text", content: rest });

  return segments;
}

// Render markdown-like bold (**text**) in plain text segments
function renderTextContent(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={i} className="font-semibold text-zinc-100">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

export function ChatBubble({ message }: ChatBubbleProps) {
  const isUser = message.role === "user";
  const segments = isUser ? null : parseContent(message.content);
  const hasCode = segments?.some((s) => s.type === "code");

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
          isUser
            ? "bg-accent text-bg"
            : "border border-line bg-panel text-zinc-200"
        }`}
      >
        {isUser || !hasCode ? (
          <div className="whitespace-pre-wrap">
            {message.content || <span className="animate-pulse text-muted">…</span>}
          </div>
        ) : (
          <div>
            {segments!.map((seg, i) =>
              seg.type === "text" ? (
                <span key={i} className="whitespace-pre-wrap">
                  {renderTextContent(seg.content)}
                </span>
              ) : (
                <CodeBlock key={i} code={seg.content} language={seg.language!} />
              ),
            )}
          </div>
        )}
        {!isUser && message.decision && (
          <DecisionBadge decision={message.decision} />
        )}
      </div>
    </div>
  );
}
