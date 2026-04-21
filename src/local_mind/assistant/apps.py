from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class App:
    name: str
    path: str
    kind: str  # 'shortcut' | 'uwp' | 'command'
    args: list[str] | None = None


_COMMON_ALIASES: dict[str, list[str]] = {
    "notepad": ["notepad"],
    "notepad++": ["notepad++"],
    "calculator": ["calc"],
    "command prompt": ["cmd"],
    "powershell": ["powershell"],
    "terminal": ["wt", "powershell"],
    "task manager": ["taskmgr"],
    "file explorer": ["explorer"],
    "explorer": ["explorer"],
    "paint": ["mspaint"],
    "snipping tool": ["snippingtool"],
    "chrome": ["chrome"],
    "google chrome": ["chrome"],
    "edge": ["msedge"],
    "firefox": ["firefox"],
    "vs code": ["code"],
    "visual studio code": ["code"],
    "cursor": ["cursor"],
    "spotify": ["spotify"],
    "discord": ["discord"],
    "slack": ["slack"],
    "steam": ["steam"],
}


class AppIndex:
    """Enumerates installed Windows apps and provides fuzzy launch/close.

    Maintains an in-memory cache with a short TTL and, when a watchdog is
    started via :meth:`start_watcher`, invalidates that cache immediately as
    soon as a new ``.lnk`` appears in the Start Menu — so newly installed
    apps become voice-addressable without waiting for the TTL to elapse.
    """

    _CACHE_TTL_SECONDS = 30.0

    def __init__(self) -> None:
        self._apps: list[App] = []
        self._loaded_at = 0.0
        self._lock = threading.Lock()
        self._dirty = True
        self._observer: Any = None
        self._listeners: list[Callable[[list[App]], None]] = []

    # ── discovery ───────────────────────────────────────────────────────────
    def refresh(self, force: bool = False) -> list[App]:
        with self._lock:
            fresh = not self._dirty and self._apps and time.time() - self._loaded_at < self._CACHE_TTL_SECONDS
        if fresh and not force:
            return self._apps
        apps: list[App] = []

        for key, cmds in _COMMON_ALIASES.items():
            for cmd in cmds:
                resolved = shutil.which(cmd)
                if resolved:
                    apps.append(App(name=key, path=resolved, kind="command"))
                    break

        if sys.platform == "win32":
            apps.extend(self._scan_windows_start_menu())
            apps.extend(self._scan_windows_uwp())

        seen: dict[str, App] = {}
        for app in apps:
            key = app.name.lower()
            if key not in seen:
                seen[key] = app
        ordered = sorted(seen.values(), key=lambda a: a.name.lower())
        with self._lock:
            self._apps = ordered
            self._loaded_at = time.time()
            self._dirty = False
        log.info("App index: %d entries", len(ordered))
        for cb in list(self._listeners):
            try:
                cb(ordered)
            except Exception as e:
                log.debug("AppIndex listener error: %s", e)
        return ordered

    def invalidate(self) -> None:
        with self._lock:
            self._dirty = True

    def force_refresh(self) -> list[App]:
        self.invalidate()
        return self.refresh(force=True)

    def add_listener(self, cb: Callable[[list[App]], None]) -> None:
        self._listeners.append(cb)

    # ── file watcher ───────────────────────────────────────────────────────
    def _watched_roots(self) -> list[Path]:
        if sys.platform != "win32":
            return []
        return [
            p for p in [
                Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
                Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            ]
            if p.is_dir()
        ]

    def start_watcher(self) -> bool:
        """Start a background watcher that invalidates the cache when the
        Start Menu changes. Returns True on success, False if watchdog isn't
        installed (in which case the TTL-based refresh still works)."""
        if self._observer is not None:
            return True
        roots = self._watched_roots()
        if not roots:
            return False
        try:
            from watchdog.events import FileSystemEventHandler  # type: ignore
            from watchdog.observers import Observer  # type: ignore
        except ImportError:
            log.info("watchdog not installed; Start Menu watcher disabled.")
            return False

        index = self

        class _Handler(FileSystemEventHandler):  # type: ignore[misc]
            def on_any_event(self, event: Any) -> None:
                path = str(getattr(event, "src_path", "") or "").lower()
                if path.endswith(".lnk") or getattr(event, "is_directory", False):
                    index.invalidate()

        obs = Observer()
        handler = _Handler()
        for root in roots:
            try:
                obs.schedule(handler, str(root), recursive=True)
            except Exception as e:
                log.debug("Could not watch %s: %s", root, e)
        obs.daemon = True
        obs.start()
        self._observer = obs
        log.info("Start Menu watcher active on %d root(s)", len(roots))
        return True

    def stop_watcher(self) -> None:
        obs = self._observer
        self._observer = None
        if obs is None:
            return
        try:
            obs.stop()
            obs.join(timeout=2.0)
        except Exception:
            pass

    def _scan_windows_start_menu(self) -> list[App]:
        out: list[App] = []
        roots = [
            Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        ]
        for root in roots:
            if not root.is_dir():
                continue
            for lnk in root.rglob("*.lnk"):
                name = lnk.stem
                if any(skip in name.lower() for skip in ("uninstall", "readme", "release notes")):
                    continue
                out.append(App(name=name, path=str(lnk), kind="shortcut"))
        return out

    def _scan_windows_uwp(self) -> list[App]:
        """Enumerate UWP / Store apps via PowerShell (best-effort, may be slow)."""
        if sys.platform != "win32":
            return []
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-StartApps | ForEach-Object { \"$($_.Name)`t$($_.AppID)\" }"],
                capture_output=True, text=True, timeout=6,
                creationflags=_no_window_flag(),
            )
        except Exception as e:
            log.debug("UWP scan failed: %s", e)
            return []
        if proc.returncode != 0:
            return []
        out: list[App] = []
        for line in (proc.stdout or "").splitlines():
            name, _, appid = line.partition("\t")
            name, appid = name.strip(), appid.strip()
            if not name or not appid:
                continue
            out.append(App(name=name, path=f"shell:AppsFolder\\{appid}", kind="uwp"))
        return out

    # ── search ──────────────────────────────────────────────────────────────
    def find(self, query: str, top: int = 5) -> list[App]:
        self.refresh()
        q = query.strip().lower()
        if not q:
            return []
        q_tokens = set(_WORD_RE.findall(q))
        scored: list[tuple[float, App]] = []
        for app in self._apps:
            n = app.name.lower()
            n_tokens = set(_WORD_RE.findall(n))
            score = 0.0
            if n == q:
                score += 100
            if n.startswith(q):
                score += 40
            if q in n:
                score += 25
            if q_tokens and q_tokens.issubset(n_tokens):
                score += 15
            overlap = len(q_tokens & n_tokens)
            score += overlap * 6
            if score > 0:
                scored.append((score, app))
        scored.sort(key=lambda x: (-x[0], len(x[1].name)))
        return [a for _, a in scored[:top]]

    # ── actions ─────────────────────────────────────────────────────────────
    def launch(self, query: str) -> tuple[App | None, str]:
        matches = self.find(query)
        if not matches:
            return None, f"I couldn't find an app named '{query}'."
        app = matches[0]
        try:
            if app.kind == "shortcut":
                os.startfile(app.path)  # type: ignore[attr-defined]
            elif app.kind == "uwp":
                os.startfile(app.path)  # type: ignore[attr-defined]
            elif app.kind == "command":
                cmd = [app.path, *(app.args or [])]
                subprocess.Popen(cmd, creationflags=_no_window_flag())
            return app, f"Opening {app.name}."
        except Exception as e:
            return app, f"Could not open {app.name}: {e}"

    def close(self, query: str) -> tuple[list[str], str]:
        try:
            import psutil
        except ImportError:
            return [], "psutil is not installed — cannot close apps."
        q = query.strip().lower()
        if not q:
            return [], "Close what?"
        killed: list[str] = []
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                pname = (proc.info.get("name") or "").lower()
                exe = (proc.info.get("exe") or "").lower()
            except Exception:
                continue
            if not pname:
                continue
            bare = os.path.splitext(pname)[0]
            if q == bare or q in pname or q in os.path.basename(exe):
                try:
                    proc.terminate()
                    killed.append(pname)
                except Exception:
                    continue
        if not killed:
            return [], f"No running process matched '{query}'."
        psutil.wait_procs(
            [p for p in psutil.process_iter(["name"]) if p.info.get("name", "").lower() in killed],
            timeout=2.0,
        )
        return killed, f"Closed {len(killed)} process{'es' if len(killed) != 1 else ''}."

    def list_running(self) -> list[dict[str, str | int]]:
        try:
            import psutil
        except ImportError:
            return []
        out = []
        for p in psutil.process_iter(["pid", "name", "username"]):
            try:
                info = p.info
                if info.get("name") and not info["name"].startswith("System"):
                    out.append({
                        "pid": info["pid"],
                        "name": info["name"],
                        "user": info.get("username") or "",
                    })
            except Exception:
                continue
        return out


def _no_window_flag() -> int:
    if sys.platform == "win32":
        return 0x08000000
    return 0
