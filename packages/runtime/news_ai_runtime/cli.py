"""Local operator boundary; no arbitrary native services and no raw diagnostics."""

import argparse
import asyncio
import json
from uuid import UUID

from news_ai_common.config import AppSettings, ConfigLoader
from news_ai_database import create_database_engine, create_session_factory
from news_ai_events import (
    DEFAULT_RECONCILIATION_LIMIT,
    MAX_RECONCILIATION_LIMIT,
    EventType,
    ReconciliationMode,
)
from news_ai_events.consumer_contracts import CONSUMER_CONTRACT_BY_EVENT

from .ai_credentials import AICredentialOperator
from .controller import RuntimeOperationError, build_controller
from .health import build_monitor
from .publishing import DatabasePublishingControl
from .reconciliation import build_reconciliation_stack


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
    events = commands.add_parser("events")
    events.add_argument("action", choices=["reconcile"])
    mode = events.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    events.add_argument("--limit", type=int, default=DEFAULT_RECONCILIATION_LIMIT)
    events.add_argument("--after-outbox-id", type=UUID)
    events.add_argument("--reason")
    events.add_argument(
        "--event-type",
        choices=sorted(event_type.value for event_type in CONSUMER_CONTRACT_BY_EVENT),
    )
    ai = commands.add_parser("ai")
    ai_commands = ai.add_subparsers(dest="ai_command", required=True)
    credentials = ai_commands.add_parser("credentials")
    credentials.add_argument("action", choices=["status", "probe", "reset"])
    credentials.add_argument("--pool")
    credentials.add_argument("--slot", type=int)
    credentials.add_argument("--reason")
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


async def reconciliation_command(stack, args):
    try:
        report = await stack.service.reconcile(
            mode=ReconciliationMode.APPLY if args.apply else ReconciliationMode.DRY_RUN,
            limit=args.limit,
            reason=args.reason,
            event_type=EventType(args.event_type) if args.event_type else None,
            after_outbox_id=args.after_outbox_id,
        )
        payload = report.model_dump(mode="json")
        return payload, 2 if report.error_code else 0
    finally:
        close = getattr(stack, "close", None)
        if close is not None:
            await close()


def main(
    argv=None,
    *,
    settings=None,
    controller=None,
    monitor=None,
    control=None,
    reconciliation_stack=None,
    credential_operator=None,
):
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
        elif args.command == "publish":
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
        elif args.command == "events":
            if not 1 <= args.limit <= MAX_RECONCILIATION_LIMIT:
                raise RuntimeOperationError("INVALID_LIMIT")
            if args.apply and not args.reason:
                raise RuntimeOperationError("REASON_REQUIRED")
            payload, code = asyncio.run(
                reconciliation_command(
                    reconciliation_stack or build_reconciliation_stack(settings), args
                )
            )
        else:
            if credential_operator is None:
                if not settings.database_url:
                    raise RuntimeOperationError("CREDENTIAL_STATE_UNAVAILABLE")
                engine = create_database_engine(settings.database_url)
                credential_operator = AICredentialOperator(
                    ConfigLoader(settings.config_dir), create_session_factory(engine)
                )
            if args.action != "status" and (not args.pool or args.slot is None):
                raise RuntimeOperationError("INVALID_ARGUMENTS")
            if args.action == "status":
                result = asyncio.run(credential_operator.status())
                payload = [item.model_dump(mode="json") for item in result]
            elif args.action == "probe":
                asyncio.run(credential_operator.probe(args.pool, args.slot))
                payload = {"status": "HEALTHY"}
            else:
                if not args.reason:
                    raise RuntimeOperationError("REASON_REQUIRED")
                result = asyncio.run(credential_operator.reset(args.pool, args.slot, args.reason))
                payload = result.model_dump(mode="json")
            code = 0
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
