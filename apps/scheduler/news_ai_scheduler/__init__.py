"""Production scheduler composition (no publisher/adapter dependencies)."""

import logging
from dataclasses import dataclass
from time import sleep

from news_ai_common.config import AppSettings, ConfigDomain, ConfigLoader
from news_ai_database import create_database_engine, create_session_factory
from news_ai_editorial import EditorialConfigLoader
from news_ai_publishing import PublicationScheduler, PublicationService, SchedulerConfig
from news_ai_publishing.contracts import system_clock
from news_ai_review import ApprovalEligibilityService
from news_ai_runtime import DatabasePublishingControl
from news_ai_social.config import load_instagram_config


@dataclass(frozen=True)
class ProductionSchedulerStack:
    service: PublicationService
    scheduler: PublicationScheduler


def build_production_scheduler_stack(
    settings: AppSettings, *, clock=system_clock, session_factory=None
):
    if not settings.database_url and session_factory is None:
        raise ValueError("scheduler database configuration unavailable")
    loader = ConfigLoader(settings.config_dir)
    policy = EditorialConfigLoader(loader).load_publishing_policy()
    config = loader.load_domain_file(ConfigDomain.PLATFORMS, "publishing.yaml", SchedulerConfig)
    factory = session_factory or create_session_factory(
        create_database_engine(settings.database_url)
    )
    service = PublicationService(
        factory,
        ApprovalEligibilityService(factory, policy),
        clock=clock,
        platform_config=load_instagram_config(settings.config_dir),
    )
    control = DatabasePublishingControl(factory, clock=clock)
    return ProductionSchedulerStack(
        service, PublicationScheduler(service, config, paused=control.paused)
    )


def run(stack: ProductionSchedulerStack, *, should_stop=lambda: False, wait=sleep):
    while not should_stop():
        try:
            stack.scheduler.scan()
        except Exception:
            # Database exceptions can contain connection strings or bound sensitive values.
            logging.getLogger(__name__).error("scheduler batch failed; durable work retained")
        wait(stack.scheduler.config.poll_interval_seconds)
