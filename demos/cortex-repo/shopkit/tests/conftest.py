import os

import pytest

from shopkit import store


@pytest.fixture
def db():
    path = os.environ.get("SHOPKIT_DB")
    if os.environ.get("APP_ENV") != "test" or not path:
        raise RuntimeError("SHOPKIT_DB is not set")
    connection = store.connect(path)
    yield connection
    for table in ("orders", "products", "users"):
        connection.execute(f"DELETE FROM {table}")
    connection.commit()
    connection.close()
