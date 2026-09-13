"""Local operator boundary; no arbitrary native services and no raw diagnostics."""

import argparse
import asyncio
import json

from news_ai_common.config import AppSettings
from news_ai_database import create_database_engine, create_session_factory

from .controller import RuntimeOperationError, build_controller
from .health import build_monitor
from .publishing import DatabasePublishingControl


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "INVALID_ARGUMENTS\n")


def parser():
    root = SafeParser(prog="newsctl")
    commands = root.add_subparsers(dest="command", required=True)
    runtime = commands.add_parser("runtime")
    runtime.add_argument("action", choices=["detect"], nargs="?", default="detect")
    service = commands.add_parser("service")
    service.add_argument(
        "action", choices=["list", "status", "start", "stop", "restart", "enable", "disable"]
    )
    service.add_argument("name", nargs="?")
    commands.add_parser("health")
    publish = commands.add_parser("publish")
    publish.add_argument("action", choices=["status", "pause", "resume"])
    publish.add_argument("--reason")
    return root


async def health_command(monitor):
    try:
        snapshot = await monitor.collect()
        return snapshot.report.model_dump(mode="json"), {
            "HEALTHY": 0,
            "DEGRADED": 1,
            "UNHEALTHY": 2,
        }[snapshot.report.status]
    finally:
        if monitor.redis_client:
            await monitor.redis_client.aclose()
        if monitor.factory:
            monitor.factory.kw["bind"].dispose()


def main(argv=None, *, settings=None, controller=None, monitor=None, control=None):
    args = parser().parse_args(argv)
    engine = None
    try:
        settings = settings or AppSettings()
        if args.command == "runtime":
            payload = (controller or build_controller(settings)).inspect()
            code = 0
        elif args.command == "service":
            controller = controller or build_controller(settings)
            if args.action == "list":
                payload = controller.list_services()
            else:
                if not args.name:
                    raise RuntimeOperationError("SERVICE_UNKNOWN")
                payload = controller.operation(args.action, args.name)
            code = 2 if isinstance(payload, dict) and payload.get("error_code") else 0
        elif args.command == "health":
            payload, code = asyncio.run(health_command(monitor or build_monitor(settings)))
        else:
            if control is None:
                if not settings.database_url:
                    raise RuntimeOperationError("CONTROL_UNAVAILABLE")
                engine = create_database_engine(settings.database_url)
                control = DatabasePublishingControl(create_session_factory(engine))
            if args.action == "status":
                snapshot = control.snapshot()
            else:
                if not args.reason:
                    raise RuntimeOperationError("REASON_REQUIRED")
                snapshot = control.set_paused(args.action == "pause", reason=args.reason)
            payload, code = snapshot.model_dump(mode="json"), 0 if snapshot.available else 2
        print(json.dumps(payload, sort_keys=True))
        return code
    except RuntimeOperationError as exc:
        print(json.dumps({"error_code": exc.code}))
        return 2
    except Exception:
        print(json.dumps({"error_code": "OPERATION_UNAVAILABLE"}))
        return 2
    finally:
        if engine:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
