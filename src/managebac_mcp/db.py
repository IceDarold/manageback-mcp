"""Database engine/session setup."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .schema import Base


class Database:
    def __init__(self, url: str, echo: bool = False):
        self.engine = create_engine(url, echo=echo, future=True)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

    def create_all(self) -> None:
        Base.metadata.create_all(bind=self.engine)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """Add columns introduced after a database was first created.

        create_all() only ever creates whole tables, so a deployed SQLite file
        keeps its old task columns forever without this.
        """
        inspector = inspect(self.engine)
        for table in Base.metadata.sorted_tables:
            if table.name not in inspector.get_table_names():
                continue
            existing = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing or not column.nullable:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(self.engine.dialect)}"
                with self.engine.begin() as conn:
                    conn.execute(text(ddl))

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
