import { useCallback, useRef, useState } from "react";
import { api } from "@/api";

export function useVoice(onTranscript: (text: string) => void) {
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const start = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream, { mimeType: "audio/webm" });
      chunksRef.current = [];
      mr.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        setTranscribing(true);
        try {
          const text = await api.transcribe(blob);
          if (text.trim()) onTranscript(text.trim());
        } catch {
          // silently ignore
        } finally {
          setTranscribing(false);
        }
      };
      mediaRef.current = mr;
      mr.start();
      setRecording(true);
    } catch {
      // mic permission denied
    }
  }, [onTranscript]);

  const stop = useCallback(() => {
    if (mediaRef.current && mediaRef.current.state !== "inactive") {
      mediaRef.current.stop();
      mediaRef.current = null;
    }
    setRecording(false);
  }, []);

  const toggle = useCallback(() => {
    if (recording) stop();
    else start();
  }, [recording, start, stop]);

  return { recording, transcribing, toggle };
}
