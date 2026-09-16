#!/usr/bin/env python3
"""Run from repository root: python scripts/serve-preview-origin.py --help."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.preview_origin_service import make_origin  # noqa: E402
from app.services.preview_package_service import PublicationStore  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description="Seller-only static preview origin; place behind your HTTPS proxy."
    )
    parser.add_argument("--public-root", required=True)
    parser.add_argument("--journal-root", required=True)
    parser.add_argument(
        "--origin", required=True, help="Intended anonymous browser HTTPS origin"
    )
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", default=8795, type=int)
    args = parser.parse_args()
    store = PublicationStore(args.public_root, args.journal_root)
    with make_origin(
        store, origin=args.origin, bind=args.bind, port=args.port
    ) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
