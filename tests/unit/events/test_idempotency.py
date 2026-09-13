from uuid import uuid4

from news_ai_database import Base
from news_ai_events.idempotency import mark_processed, was_processed
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_processed_event_marker_is_scoped_to_consumer_group() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    event_id = uuid4()

    with factory() as session, session.begin():
        assert was_processed(session, event_id=event_id, consumer_group="processor") is False
        mark_processed(
            session,
            event_id=event_id,
            consumer_group="processor",
            result={"status": "ok"},
        )

    with factory() as session:
        assert was_processed(session, event_id=event_id, consumer_group="processor") is True
        assert was_processed(session, event_id=event_id, consumer_group="analytics") is False
