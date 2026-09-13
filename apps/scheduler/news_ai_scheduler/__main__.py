"""Run the bounded scheduler; publication execution belongs to a later stage."""

from contextlib import suppress

from news_ai_common.config import AppSettings

from . import build_production_scheduler_stack, run


def main() -> None:
    stack = build_production_scheduler_stack(AppSettings())
    with suppress(KeyboardInterrupt):
        run(stack)


if __name__ == "__main__":
    main()
