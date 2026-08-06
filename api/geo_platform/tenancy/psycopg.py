from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import Connection


@contextmanager
def tenant_connection(
    dsn: str,
    tenant_pub_id: str,
    **kwargs: Any,
) -> Iterator[Connection[Any]]:
    """Open a transaction-scoped psycopg connection with fail-closed S02 RLS."""
    with psycopg.connect(dsn, **kwargs) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_pub_id', %s, true)",
            (tenant_pub_id,),
        )
        yield connection
