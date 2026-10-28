import pytest
from pydantic import ValidationError

from campus_parking.core.settings import Settings, get_settings

KEY = "k" * 32


def build(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_defaults() -> None:
    s = build()
    assert s.env == "dev"
    assert s.timezone == "America/Fortaleza"
    assert s.ingest_max_batch == 1000
    assert s.ingest_api_key is None


def test_cors_wildcard_only_in_dev() -> None:
    assert build(env="dev", cors_origins=["*"]).cors_origins == ["*"]
    with pytest.raises(ValidationError, match="only when env is dev"):
        build(env="test", cors_origins=["*"])


def test_key_min_length() -> None:
    assert build(ingest_api_key=KEY).ingest_api_key is not None
    with pytest.raises(ValidationError, match="at least 32"):
        build(admin_api_key="short")


def test_secret_not_in_repr() -> None:
    assert KEY not in repr(build(operator_api_key=KEY))


def test_prod_requires_all_keys() -> None:
    with pytest.raises(ValidationError, match="prod requires"):
        build(env="prod", ingest_api_key=KEY)
    ok = build(
        env="prod", ingest_api_key="i" * 32, admin_api_key="a" * 32, operator_api_key="o" * 32
    )
    assert ok.env == "prod"


def test_prod_rejects_sqlite() -> None:
    with pytest.raises(ValidationError, match="SQLite"):
        build(
            env="prod",
            database_url="sqlite+aiosqlite:///x.db",
            ingest_api_key="i" * 32,
            admin_api_key="a" * 32,
            operator_api_key="o" * 32,
        )


def test_timezone_validated() -> None:
    assert build(timezone="UTC").tzinfo.key == "UTC"
    with pytest.raises(ValidationError, match="unknown time zone"):
        build(timezone="Mars/Olympus")


def test_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARKING_ENV", "test")
    monkeypatch.setenv("PARKING_CORS_ORIGINS", '["http://a.test"]')
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.env == "test"
        assert s.cors_origins == ["http://a.test"]
    finally:
        get_settings.cache_clear()


def test_prod_rejects_placeholder_keys() -> None:
    placeholder = "change-me-to-a-random-32+-char-key"
    with pytest.raises(ValidationError, match="placeholders"):
        build(
            env="prod",
            ingest_api_key=placeholder,
            admin_api_key="a" * 32,
            operator_api_key="o" * 32,
        )


def test_prod_requires_distinct_keys() -> None:
    with pytest.raises(ValidationError, match="must all differ"):
        build(env="prod", ingest_api_key=KEY, admin_api_key=KEY, operator_api_key="o" * 32)
    ok = build(
        env="prod", ingest_api_key="i" * 32, admin_api_key="a" * 32, operator_api_key="o" * 32
    )
    assert ok.env == "prod"
