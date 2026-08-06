from collections.abc import Iterator

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema="platform", naming_convention=NAMING_CONVENTION)


settings = get_settings()
database_dsn = settings.runtime_postgres_dsn or settings.postgres_dsn
if database_dsn.startswith("postgresql://"):
    database_dsn = database_dsn.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(
    database_dsn,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

worker_database_dsn = settings.worker_postgres_dsn or settings.postgres_dsn
if worker_database_dsn.startswith("postgresql://"):
    worker_database_dsn = worker_database_dsn.replace("postgresql://", "postgresql+psycopg://", 1)
worker_engine = create_engine(worker_database_dsn, pool_pre_ping=True)
WorkerSessionLocal = sessionmaker(bind=worker_engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
