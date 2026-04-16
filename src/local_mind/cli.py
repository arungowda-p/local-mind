from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="localmind", description="LocalMind — local LLM assistant")
    sub = parser.add_subparsers(dest="command")

    srv = sub.add_parser("serve", help="Start the web server + UI")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8766)

    dl = sub.add_parser("download", help="Pre-download a model by name")
    dl.add_argument("model", help="Model name (e.g. tinyllama-1.1b, phi-3-mini, qwen2.5-1.5b)")

    sub.add_parser("models", help="List available models")

    args = parser.parse_args()

    if args.command == "serve":
        from local_mind.server import run_server

        run_server(host=args.host, port=args.port)
    elif args.command == "download":
        from local_mind.models import ensure_model

        path = ensure_model(args.model)
        print(f"Model ready: {path}")
    elif args.command == "models":
        from local_mind.models import KNOWN_MODELS, model_manager

        for entry in model_manager.list_available():
            dl = "downloaded" if entry["downloaded"] else "not downloaded"
            sz = f" ({entry['size_mb']} MB)" if entry["size_mb"] else ""
            print(f"  {entry['name']:20s}  {dl}{sz}")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
