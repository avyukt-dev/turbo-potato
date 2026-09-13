"""Per-scrape registry: aggregate gauges only, with no process-global registration."""

from prometheus_client import CollectorRegistry, Gauge, generate_latest

from .health import build_monitor


def render_metrics(snapshot):
    registry, gauges = CollectorRegistry(), {}
    for sample in snapshot.samples:
        key = (sample.name, tuple(sorted(sample.labels)))
        if key not in gauges:
            gauges[key] = Gauge(
                f"news_ai_{sample.name}",
                "Read-only operational snapshot",
                labelnames=key[1],
                registry=registry,
            )
        gauge = gauges[key]
        if sample.labels:
            gauge = gauge.labels(**sample.labels)
        gauge.set(sample.value)
    return generate_latest(registry)


async def collect_metrics(settings):
    monitor = None
    try:
        monitor = build_monitor(settings)
        return render_metrics(await monitor.collect(deep=False))
    except Exception:
        # Even invalid configuration must not leak diagnostic details or crash a scrape.
        registry = CollectorRegistry()
        gauge = Gauge(
            "news_ai_component_ready", "Dependency availability", ["component"], registry=registry
        )
        for component in ("postgres", "redis", "ai_router"):
            gauge.labels(component=component).set(0)
        return generate_latest(registry)
    finally:
        if monitor:
            try:
                if monitor.redis_client:
                    await monitor.redis_client.aclose()
            except Exception:
                pass  # A scrape still returns valid exposition during cleanup failure.
            finally:
                if monitor.factory:
                    monitor.factory.kw["bind"].dispose()
