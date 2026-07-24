"""Seed the authoritative role and permission catalogue.

Revision ID: s01_0004
Revises: s01_0003
"""

from collections.abc import Sequence

from alembic import op
from geo_platform.identity.policy import ROLE_PERMISSIONS

revision: str = "s01_0004"
down_revision: str | Sequence[str] | None = "s01_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def stable_uuid(kind: str, name: str) -> str:
    return f"md5('geo-s01:{kind}:{name}')::uuid"


def quote(value: str) -> str:
    return value.replace("'", "''")


def upgrade() -> None:
    permissions = sorted({item for values in ROLE_PERMISSIONS.values() for item in values})
    for permission in permissions:
        escaped = quote(permission)
        op.execute(
            'INSERT INTO platform."permission" (id, name, description) '
            f"VALUES ({stable_uuid('permission', permission)}, '{escaped}', "
            f"'GEO Platform permission {escaped}') ON CONFLICT (name) DO NOTHING"
        )
    for role, role_permissions in ROLE_PERMISSIONS.items():
        role_name = quote(role.value)
        op.execute(
            'INSERT INTO platform."role" (id, name, description) '
            f"VALUES ({stable_uuid('role', role.value)}, '{role_name}', "
            f"'GEO Platform {role_name} role') ON CONFLICT (name) DO NOTHING"
        )
        for permission in sorted(role_permissions):
            op.execute(
                'INSERT INTO platform."role_permission" (id, role_id, permission_id) '
                f"VALUES ({stable_uuid('role-permission', f'{role.value}:{permission}')}, "
                f"{stable_uuid('role', role.value)}, {stable_uuid('permission', permission)}) "
                "ON CONFLICT (role_id, permission_id) DO NOTHING"
            )


def downgrade() -> None:
    role_names = ", ".join(f"'{quote(role.value)}'" for role in ROLE_PERMISSIONS)
    permission_names = ", ".join(
        f"'{quote(permission)}'"
        for permission in sorted({item for values in ROLE_PERMISSIONS.values() for item in values})
    )
    op.execute(
        'DELETE FROM platform."role_permission" WHERE role_id IN '
        f'(SELECT id FROM platform."role" WHERE name IN ({role_names}))'
    )
    op.execute(f'DELETE FROM platform."role" WHERE name IN ({role_names})')
    op.execute(f'DELETE FROM platform."permission" WHERE name IN ({permission_names})')
