export type Intent = "question" | "action" | "learn" | "chitchat" | "clarify" | "code";
export type ActionType = "rag_chat" | "direct_chat" | "learn_url" | "summarize" | "clarify" | "write_code" | "run_code";

export interface DecisionMeta {
  intent: { intent: Intent; confidence: number; scores: Record<string, number> };
  confidence: {
    confidence: number;
    has_context: boolean;
    recommendation: string;
    detail: string;
    top_similarity?: number;
    context_chunks?: number;
  };
  action: { action: ActionType; score: number; reasoning: string; all_scores: Record<string, number> };
}

export interface CodeResult {
  language: string;
  exit_code: number;
  stdout: string;
  stderr: string;
  timed_out: boolean;
  duration_ms: number;
  ok: boolean;
}

export interface WebSource {
  url: string;
  title: string;
  snippet?: string;
  status?: string;
  chunks?: number;
  reason?: string;
}

export interface WebSearchSummary {
  status: "learned" | "fetched" | "no_results" | string;
  query: string;
  chunks?: number;
  sources: WebSource[];
}

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
  decision?: DecisionMeta;
  codeResults?: CodeResult[];
  webSources?: WebSearchSummary;
}

export interface ModelInfo {
  name: string;
  repo: string;
  file: string;
  downloaded: boolean;
  loaded: boolean;
  size_mb: number | null;
}

export interface KnowledgeStats {
  collection: string;
  documents: number;
}

export interface HealthStatus {
  status: string;
  model_loaded: boolean;
  model_name: string | null;
  knowledge_docs: number;
}

export interface TranscriptSegment {
  index: number;
  start: number;
  end: number;
  text: string;
}

export interface TranscriptInfo {
  language: string;
  language_probability: number;
  duration: number;
}

export interface TranscribeStreamCallbacks {
  onInfo?: (info: TranscriptInfo) => void;
  onSegment?: (seg: TranscriptSegment) => void;
  onError?: (msg: string) => void;
  onDone?: (text: string) => void;
  signal?: AbortSignal;
}

export interface TranscribeOptions {
  language?: string;
  model?: string;
}

export type AssistantState =
  | "stopped"
  | "starting"
  | "idle"
  | "listening"
  | "processing"
  | "speaking"
  | "error";

export interface AssistantStatus {
  running: boolean;
  state: AssistantState;
  wake_available: boolean;
  tts_available: boolean;
  last_transcript: string;
  app_count: number;
  hotkey: string;
  wake_word: string;
}

export interface AssistantActionResult {
  ok: boolean;
  speech: string;
  detail?: string;
  data?: Record<string, unknown>;
}

export interface AssistantEvent {
  ts: number;
  kind:
    | "state"
    | "wake"
    | "listening"
    | "level"
    | "transcript"
    | "action"
    | "speech"
    | "info"
    | "apps"
    | "error";
  [key: string]: unknown;
}

export interface AssistantCommandResponse {
  ok: boolean;
  speech: string;
  detail: string;
  intent: string;
  data: Record<string, unknown>;
}

export interface AssistantApp {
  name: string;
  kind: "shortcut" | "uwp" | "command";
  path: string;
}
