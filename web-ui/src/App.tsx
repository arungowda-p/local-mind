import { useCallback, useEffect, useState } from "react";
import { api } from "@/api";
import { AssistantPage } from "@/components/AssistantPage";
import { ChatInput } from "@/components/ChatInput";
import { ChatWindow } from "@/components/ChatWindow";
import { Sidebar } from "@/components/Sidebar";
import { TopBar, type View } from "@/components/TopBar";
import { TranscribePage } from "@/components/TranscribePage";
import { useChat } from "@/hooks/useChat";
import { useVoice } from "@/hooks/useVoice";

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [modelName, setModelName] = useState<string | null>(null);
  const [view, setView] = useState<View>("chat");
  const { messages, streaming, send, stop, clear } = useChat();

  useEffect(() => {
    api.health().then((h) => setModelName(h.model_name)).catch(() => {});
  }, []);

  const refreshModel = useCallback(() => {
    api.health().then((h) => setModelName(h.model_name)).catch(() => {});
  }, []);

  const handleSend = useCallback(
    (text: string) => {
      send(text, true, 0.7);
      refreshModel();
    },
    [send, refreshModel],
  );

  const { recording, transcribing, toggle: toggleVoice } = useVoice(handleSend);

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar open={sidebarOpen} onClose={() => { setSidebarOpen(false); refreshModel(); }} />
      <main className="flex flex-1 flex-col overflow-hidden">
        <TopBar
          modelName={modelName}
          view={view}
          onViewChange={setView}
          onMenuClick={() => setSidebarOpen((v) => !v)}
          onClearChat={clear}
        />
        {view === "chat" ? (
          <>
            <ChatWindow messages={messages} />
            <ChatInput
              onSend={handleSend}
              onStop={stop}
              streaming={streaming}
              voiceRecording={recording}
              voiceTranscribing={transcribing}
              onVoiceToggle={toggleVoice}
              disabled={!modelName}
            />
          </>
        ) : view === "transcribe" ? (
          <TranscribePage />
        ) : (
          <AssistantPage />
        )}
      </main>
    </div>
  );
}
