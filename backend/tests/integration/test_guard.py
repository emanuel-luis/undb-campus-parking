"""The integration fixtures refuse databases they could destroy (no Postgres needed)."""

import pytest

from tests.integration.conftest import check_throwaway_database

TEST_URL = "postgresql+asyncpg://parking:secret@localhost:5432/parking_test"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://parking:secret@localhost:5432/parking",
        "postgresql+asyncpg://parking:secret@localhost:5432/postgres",
        "postgresql+asyncpg://parking:secret@localhost:5432/test_parking",
    ],
)
def test_refuses_a_database_not_named_test(url: str) -> None:
    with pytest.raises(pytest.exit.Exception, match="must end with '_test'"):
        check_throwaway_database(url, None)


def test_refuses_the_application_database() -> None:
    with pytest.raises(pytest.exit.Exception, match="application database"):
        check_throwaway_database(TEST_URL, TEST_URL.replace("secret", "other"))


def test_accepts_a_throwaway_database() -> None:
    check_throwaway_database(TEST_URL, "postgresql+asyncpg://parking:secret@localhost:5432/parking")
    check_throwaway_database(TEST_URL, None)
