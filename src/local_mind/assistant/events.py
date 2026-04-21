from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Any


class EventBus:
    """Thread-safe fan-out event bus for assistant state.

    Subscribers each get their own queue; publish() pushes to all of them
    non-blockingly (full queues drop oldest).
    """

    def __init__(self, history: int = 200) -> None:
        self._subs: list[queue.Queue[dict[str, Any]]] = []
        self._lock = threading.Lock()
        self._history: deque[dict[str, Any]] = deque(maxlen=history)

    def publish(self, kind: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        evt = {"ts": time.time(), "kind": kind, **(data or {})}
        with self._lock:
            self._history.append(evt)
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(evt)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(evt)
                except queue.Empty:
                    pass
        return evt

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=512)
        with self._lock:
            self._subs.append(q)
            for evt in self._history:
                try:
                    q.put_nowait(evt)
                except queue.Full:
                    break
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)
