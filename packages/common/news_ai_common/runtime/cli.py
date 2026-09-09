"""Portable operator CLI for runtime inspection and service control."""

import argparse
from dataclasses import asdict
import json
import sys

from .base import UnsupportedOperation
from .detector import RuntimeDetector


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="newsctl")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("runtime", help="inspect detected runtime capabilities")

    service = subparsers.add_parser("service", help="control a service through the detected adapter")
    service.add_argument("action", choices=["status", "start", "stop", "restart", "enable", "disable"])
    service.add_argument("name")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    detector = RuntimeDetector()

    if args.command == "runtime":
        print(json.dumps(asdict(detector.inspect()), indent=2, sort_keys=True))
        return 0

    manager = detector.detect_service_manager()
    try:
        result = getattr(manager, args.action)(args.name)
    except (UnsupportedOperation, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "manager": manager.name,
                "service": args.name,
                "action": args.action,
                "state": result.state,
                "returncode": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
