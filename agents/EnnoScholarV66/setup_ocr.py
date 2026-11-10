"""Install pinned official Tesseract language data once for this server."""
import argparse
import json
from pathlib import Path

from .config import Config
from .ocr import install


def main():
    parser = argparse.ArgumentParser(description="Configurer l'OCR local EnnoScholar")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--languages", nargs="+", default=["eng", "fra"])
    args = parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    config = Config.from_env()
    if args.data_dir:
        config = config.model_copy(update={"data_dir": args.data_dir})
    print(json.dumps(install(config, args.languages), ensure_ascii=False))


if __name__ == "__main__":
    main()
