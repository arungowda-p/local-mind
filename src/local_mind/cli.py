from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(prog="localmind", description="LocalMind — local LLM assistant")
    sub = parser.add_subparsers(dest="command")

    srv = sub.add_parser("serve", help="Start the web server + UI")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8766)

    dl = sub.add_parser("download", help="Pre-download a model by name")
    dl.add_argument("model", help="Model name (e.g. tinyllama-1.1b, phi-3-mini, qwen2.5-1.5b)")

    sub.add_parser("models", help="List available models")

    trans = sub.add_parser("transcribe", help="Transcribe an audio file to text using Whisper")
    trans.add_argument("path", help="Path to audio file (.wav, .mp3, .m4a, .ogg, .webm, …)")
    trans.add_argument(
        "--language", "-l",
        default=None,
        help="ISO language code (e.g. 'en'). Auto-detect if omitted.",
    )
    trans.add_argument(
        "--model", "-m",
        default=None,
        help="Whisper model size (tiny, base, small, medium, large-v3, …). Defaults to config.",
    )
    trans.add_argument(
        "--output", "-o",
        default=None,
        help="Write final transcript to file instead of stdout.",
    )
    trans.add_argument(
        "--format", "-f",
        choices=["text", "srt"],
        default="text",
        help="Output format (default: text).",
    )
    trans.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Don't stream segments to stderr while transcribing.",
    )

    assist = sub.add_parser(
        "assistant",
        help="Run the JARVIS-style voice assistant in the foreground (headless).",
    )
    assist.add_argument("--no-confirm", action="store_true", help="Skip confirmation for sensitive commands.")
    assist.add_argument("--command", "-c", default=None, help="Run one command and exit (no voice loop).")
    assist.add_argument("--no-speak", action="store_true", help="Don't speak results (with --command).")

    args = parser.parse_args()

    if args.command == "serve":
        from local_mind.server import run_server

        run_server(host=args.host, port=args.port)
    elif args.command == "download":
        from local_mind.models import ensure_model

        path = ensure_model(args.model)
        print(f"Model ready: {path}")
    elif args.command == "models":
        from local_mind.models import model_manager

        for entry in model_manager.list_available():
            dl = "downloaded" if entry["downloaded"] else "not downloaded"
            sz = f" ({entry['size_mb']} MB)" if entry["size_mb"] else ""
            print(f"  {entry['name']:20s}  {dl}{sz}")
    elif args.command == "transcribe":
        _run_transcribe(args)
    elif args.command == "assistant":
        _run_assistant(args)
    else:
        parser.print_help()
        sys.exit(1)


def _fmt_ts(seconds: float, srt: bool = False) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds * 1000) % 1000)
    sep = "," if srt else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _run_transcribe(args: argparse.Namespace) -> None:
    src = Path(args.path)
    if not src.is_file():
        print(f"error: file not found: {src}", file=sys.stderr)
        sys.exit(2)

    try:
        from local_mind.voice import transcribe_stream
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(3)

    audio_bytes = src.read_bytes()

    segments: list[dict] = []
    info: dict | None = None

    try:
        for evt in transcribe_stream(
            audio_bytes,
            language=args.language,
            model_size=args.model,
            filename=src.name,
        ):
            t = evt["type"]
            if t == "info":
                info = evt
                if not args.quiet:
                    print(
                        f"[lang={evt['language']} "
                        f"prob={evt['language_probability']:.2f} "
                        f"duration={evt['duration']:.1f}s]",
                        file=sys.stderr,
                        flush=True,
                    )
            elif t == "segment":
                segments.append(evt)
                if not args.quiet:
                    print(
                        f"  {_fmt_ts(evt['start'])} → {_fmt_ts(evt['end'])}  {evt['text']}",
                        file=sys.stderr,
                        flush=True,
                    )
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.format == "srt":
        out = ""
        for i, seg in enumerate(segments, 1):
            out += (
                f"{i}\n"
                f"{_fmt_ts(seg['start'], srt=True)} --> {_fmt_ts(seg['end'], srt=True)}\n"
                f"{seg['text']}\n\n"
            )
    else:
        out = " ".join(s["text"] for s in segments).strip() + "\n"

    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        if not args.quiet:
            n_chars = len(out.rstrip())
            n_segs = len(segments)
            lang = info["language"] if info else "?"
            print(
                f"\nWrote {n_chars} chars / {n_segs} segments (lang={lang}) to {args.output}",
                file=sys.stderr,
            )
    else:
        sys.stdout.write(out)
        sys.stdout.flush()


def _run_assistant(args: argparse.Namespace) -> None:
    import queue as _queue

    from local_mind.assistant import get_engine
    from local_mind.config import settings

    if args.no_confirm:
        settings.assistant_require_confirmation = False

    engine = get_engine()

    if args.command:
        result = engine.run_command(args.command, speak=not args.no_speak)
        print(result.get("speech", ""))
        if result.get("detail") and result["detail"] != result.get("speech"):
            print(result["detail"])
        sys.exit(0 if result.get("ok") else 1)

    def _print(evt: dict) -> None:
        kind = evt.get("kind", "?")
        if kind == "state":
            print(f"[state] {evt.get('state')}")
        elif kind == "wake":
            print(f"[wake]  {evt.get('model')} ({evt.get('score', 0):.2f})")
        elif kind == "transcript":
            print(f"[you]   {evt.get('text')}")
        elif kind == "action":
            result = evt.get("result", {})
            ok = "ok" if result.get("ok") else "fail"
            print(f"[do]    {evt.get('intent')} [{ok}] — {result.get('speech', '')}")
        elif kind == "speech":
            pass
        elif kind == "error":
            print(f"[err]   {evt.get('message')}", file=sys.stderr)
        elif kind == "info":
            print(f"[info]  {evt.get('message')}")

    q = engine.events.subscribe()
    engine.start()
    status = engine.status()
    print("─────────────────────────────────────────────")
    print(" LocalMind Assistant — press Ctrl+C to stop.")
    print(f"   wake word : {status['wake_word']}   (available: {status['wake_available']})")
    print(f"   hotkey    : {status['hotkey']}")
    print(f"   apps known: {status['app_count']}")
    print("─────────────────────────────────────────────")

    try:
        while True:
            try:
                evt = q.get(timeout=0.5)
            except _queue.Empty:
                continue
            _print(evt)
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        engine.stop()


if __name__ == "__main__":
    main()
