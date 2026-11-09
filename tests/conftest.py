import pytest

from regression_triage.data import build


@pytest.fixture(scope="session")
def bank_db(tmp_path_factory):
    return build(tmp_path_factory.mktemp("demo") / "bank.db")


@pytest.fixture
def anyio_backend():
    return "asyncio"
