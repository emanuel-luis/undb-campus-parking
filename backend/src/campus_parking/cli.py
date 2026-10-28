"""Command line entry point: `campus-parking <command>`. Later PRs add worker, migrate, ..."""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from campus_parking.core.logging import configure_logging
from campus_parking.core.settings import get_settings


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = get_settings()
    configure_logging(settings)
    uvicorn.run(
        "campus_parking.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        timeout_graceful_shutdown=15,
        access_log=False,
        log_config=None,
    )
    return 0


def _openapi(args: argparse.Namespace) -> int:
    from campus_parking.main import create_app

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
