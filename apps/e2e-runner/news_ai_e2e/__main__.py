"""CLI for the explicit one-shot LIVE Instagram E2E run."""

import asyncio
import logging

from .configuration import LiveE2EConfigurationError
from .runner import LiveE2EError, run_live_e2e


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        result = asyncio.run(run_live_e2e())
    except (LiveE2EConfigurationError, LiveE2EError) as exc:
        logging.getLogger(__name__).error("live E2E stopped: %s", exc)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        logging.getLogger(__name__).warning("live E2E interrupted; publishing is returned to pause")
        raise SystemExit(130) from None
    except Exception:
        logging.getLogger(__name__).error("live E2E failed; inspect durable diagnostics")
        raise SystemExit(1) from None

    if result.outcome == "PUBLISHED":
        print("\nLIVE E2E PASSED")
        print(f"content_variant_id={result.content_variant_id}")
        print(f"publication_id={result.publication_id}")
        print(f"external_post_id={result.external_post_id}")
        if result.external_url:
            print(f"external_url={result.external_url}")
        return

    print(f"\nLIVE E2E STOPPED BY HUMAN DECISION: {result.outcome}")
    print(f"content_variant_id={result.content_variant_id}")
    raise SystemExit(2)


if __name__ == "__main__":
    main()
