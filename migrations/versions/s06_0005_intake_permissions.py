"""Seed intake permissions into the role and permission catalogue.

Revision ID: s06_0005
Revises: s06_0004
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s06_0005"
down_revision: str | Sequence[str] | None = "s06_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 identity/policy.py ROLE_PERMISSIONS 保持一致（幂等插入，s01_0004 样板）。
_INTAKE_PERMISSION_ROLES: dict[str, tuple[str, ...]] = {
    "intake:read": ("customer", "operator", "analyst", "reviewer"),
    "intake:write": ("customer", "operator"),
}


def stable_uuid(kind: str, name: str) -> str:
    return f"md5('geo-s01:{kind}:{name}')::uuid"


def quote(value: str) -> str:
    return value.replace("'", "''")


def upgrade() -> None:
    for permission, roles in _INTAKE_PERMISSION_ROLES.items():
        escaped = quote(permission)
        op.execute(
            'INSERT INTO platform."permission" (id, name, description) '
            f"VALUES ({stable_uuid('permission', permission)}, '{escaped}', "
            f"'GEO Platform permission {escaped}') ON CONFLICT (name) DO NOTHING"
        )
        for role in roles:
            role_name = quote(role)
            op.execute(
                'INSERT INTO platform."role" (id, name, description) '
                f"VALUES ({stable_uuid('role', role)}, '{role_name}', "
                f"'GEO Platform {role_name} role') ON CONFLICT (name) DO NOTHING"
            )
            op.execute(
                'INSERT INTO platform."role_permission" (id, role_id, permission_id) '
                f"VALUES ({stable_uuid('role-permission', f'{role}:{permission}')}, "
                f"{stable_uuid('role', role)}, {stable_uuid('permission', permission)}) "
                "ON CONFLICT (role_id, permission_id) DO NOTHING"
            )


def downgrade() -> None:
    permission_names = ", ".join(f"'{quote(p)}'" for p in _INTAKE_PERMISSION_ROLES)
    op.execute(
        'DELETE FROM platform."role_permission" WHERE permission_id IN '
        f'(SELECT id FROM platform."permission" WHERE name IN ({permission_names}))'
    )
    op.execute(f'DELETE FROM platform."permission" WHERE name IN ({permission_names})')
