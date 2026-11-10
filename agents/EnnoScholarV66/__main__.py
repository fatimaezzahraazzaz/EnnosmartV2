"""Standalone terminal entry point; the backend/frontend are optional adapters."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Config
from .service import Service


def main():
    parser = argparse.ArgumentParser(description="EnnoScholar V66 — chat scientifique indépendant")
    parser.add_argument("--env-file", type=Path, help="Fichier .env chargé explicitement, sans modifier l'environnement existant")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--session", help="Reprendre une conversation locale")
    parser.add_argument("--file", type=Path, help="Importer un PDF, TXT ou MD avant la question")
    parser.add_argument("--message", help="Une demande unique ; sinon conversation interactive")
    parser.add_argument("--no-web", action="store_true")
    args = parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    config = Config.from_env()
    updates = {}
    if args.data_dir:
        updates["data_dir"] = args.data_dir
    if args.no_web:
        updates["web_enabled"] = False
    config = config.model_copy(update=updates)
    service = Service(config)
    try:
        session = service.memory.owned(0, args.session) if args.session else service.create(0)
        sid = session["id"]
        print("Conversation locale :", sid)
        if args.file:
            print("Document :", service.upload(0, sid, args.file.read_bytes(), args.file.name)["title"])
        while True:
            message = args.message or input("\nVous > ").strip()
            if not message or message.casefold() in {"quitter", "exit", "/quit"}:
                break
            run = service.memory.start_run(sid, message, config.model, config.max_seconds + 60)
            answer = service.agent.run(sid, run["id"], session["context"])
            print("\n" + answer["answer"])
            print("\nConsommation estimée :", json.dumps(answer["usage"], ensure_ascii=False))
            if args.message:
                return 0 if answer["status"] == "completed" else 1
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
