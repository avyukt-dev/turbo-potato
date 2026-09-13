from news_ai_database.session import create_database_engine, create_session_factory
from sqlalchemy.orm import Session


def test_session_factory_is_explicit_and_usable() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    factory = create_session_factory(engine)

    with factory() as session:
        assert isinstance(session, Session)


def test_empty_database_url_is_rejected() -> None:
    try:
        create_database_engine("")
    except ValueError as exc:
        assert "must not be empty" in str(exc)
    else:
        raise AssertionError("empty database URL should fail")
