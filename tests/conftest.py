import os

import pytest

from cip.config import Settings
from cip.store import Store


@pytest.fixture
def db():
    # CI sets CIP_INTEGRATION=1; absence is an explicit local unit-only mode.
    if os.getenv("CIP_INTEGRATION") != "1":
        pytest.skip("set CIP_INTEGRATION=1 with PostgreSQL/Redis to run infrastructure tests")
    store = Store(Settings.load())
    store.migrate()
    return store


@pytest.fixture
def tenant(db):
    return db.create_tenant()
