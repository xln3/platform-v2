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
    """Open a transaction-scoped connection with both tenant RLS identities.

    Analytics-era tables scope rows by the public tenant identifier while the
    original platform tables (including projects, brands and competitors) use
    the internal UUID.  Workers routinely join both families, so setting only
    one identity makes the other family look empty.  Resolve the UUID from the
    non-RLS tenant registry and keep an unknown public identifier fail-closed by
    setting ``app.tenant_id`` to the empty string.
    """
    with psycopg.connect(dsn, **kwargs) as connection:
        connection.execute(
            """
            SELECT set_config('app.tenant_pub_id', %s, true),
                   set_config(
                     'app.tenant_id',
                     COALESCE(
                       (SELECT tenant.id::text
                        FROM platform.tenant tenant
                        WHERE tenant.pub_id=%s),
                       ''
                     ),
                     true
                   )
            """,
            (tenant_pub_id, tenant_pub_id),
        )
        yield connection
