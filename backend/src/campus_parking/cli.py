"""Command line entry point: `campus-parking <command>`. Later PRs add worker, close-day, ..."""

import argparse
import json
import os
import socket
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import FrameType

import uvicorn
from alembic.config import Config
from fastapi import FastAPI

from alembic import command
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

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        """uvicorn closes every websocket with 1012 before the lifespan shutdown runs, so the
        live sockets are told 1001 here, first."""
        self.parking_app.state.draining = True
        hub = getattr(self.parking_app.state, "hub", None)
        if hub is not None:
            await hub.close()
        await super().shutdown(sockets)


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


DEFAULT_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def alembic_config(ini: Path | None = None) -> Config:
    """Alembic config for backend/alembic.ini (or `ini`), valid from any working directory.

    The script location in the ini is relative, so it is pinned to the ini's own folder.
    """
    path = Path(ini or os.environ.get("ALEMBIC_INI") or DEFAULT_ALEMBIC_INI).resolve()
    config = Config(str(path))
    config.set_main_option("script_location", str(path.parent / "alembic"))
    return config


def _migrate(args: argparse.Namespace) -> int:
    command.upgrade(alembic_config(args.config), args.revision)
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

    migrate = sub.add_parser("migrate", help="apply database migrations (alembic upgrade)")
    migrate.add_argument("--revision", default="head", help="target revision (default: head)")
    migrate.add_argument(
        "--config", type=Path, help="alembic.ini to use (default: $ALEMBIC_INI or backend/)"
    )
    migrate.set_defaults(func=_migrate)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    func: Callable[[argparse.Namespace], int] = args.func
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
