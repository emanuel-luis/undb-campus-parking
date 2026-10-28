"""Command line entry point: `campus-parking <command>`. Later PRs add worker, migrate, ..."""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import FrameType

import uvicorn
from fastapi import FastAPI

from campus_parking.core.logging import configure_logging
from campus_parking.core.settings import get_settings
from campus_parking.main import create_app


class DrainingServer(uvicorn.Server):
    """Flips the app's draining flag on the first signal, so /readyz answers 503 while uvicorn
    still closes the listener and waits for connections (the lifespan only runs after that)."""

    def __init__(self, config: uvicorn.Config, app: FastAPI) -> None:
        super().__init__(config)
        self.parking_app = app

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        self.parking_app.state.draining = True
        super().handle_exit(sig, frame)


def _serve(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings)
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        timeout_graceful_shutdown=15,
        access_log=False,
        log_config=None,
    )
    DrainingServer(config, app).run()
    return 0


def _openapi(args: argparse.Namespace) -> int:
    schema = create_app().openapi()
    text = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="campus-parking")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API with uvicorn")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=_serve)

    openapi = sub.add_parser("openapi", help="dump the OpenAPI document as JSON")
    openapi.add_argument("--output", "-o", help="write to this file instead of stdout")
    openapi.set_defaults(func=_openapi)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    func: Callable[[argparse.Namespace], int] = args.func
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
