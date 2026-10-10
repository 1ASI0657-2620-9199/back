"""Transaction-scoped connections for local SQLite and hosted PostgreSQL."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import ssl
from urllib.parse import parse_qs, unquote, urlsplit


class QueryResult:
    def __init__(self, cursor):
        self.cursor = cursor

    def _mapping(self, row):
        if row is None:
            return None
        return dict(zip((column[0] for column in self.cursor.description), row))

    def fetchone(self):
        return self._mapping(self.cursor.fetchone())

    def fetchall(self):
        return [self._mapping(row) for row in self.cursor.fetchall()]


class DatabaseSession:
    def __init__(self, connection, postgres: bool):
        self.connection = connection
        self.postgres = postgres

    def execute(self, statement: str, parameters=()):
        cursor = self.connection.cursor()
        # Repository statements use positional placeholders and contain no
        # literal question marks. Values always remain bound parameters.
        if self.postgres:
            statement = statement.replace("?", "%s")
        cursor.execute(statement, parameters)
        return QueryResult(cursor)


class Database:
    def __init__(self, database_path: str, database_url: str | None = None):
        self.database_path = database_path
        self.database_url = database_url
        self.postgres = database_url is not None
        if self.postgres:
            parsed = urlsplit(database_url)
            if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
                raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")

    def prepare_directory(self):
        if not self.postgres and self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)

    def _open(self):
        if not self.postgres:
            return sqlite3.connect(self.database_path, timeout=10)

        import pg8000.dbapi

        parsed = urlsplit(self.database_url)
        options = parse_qs(parsed.query)
        sslmode = options.get("sslmode", ["verify-full"])[0]
        if sslmode == "disable":
            if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Unencrypted PostgreSQL is allowed only for local validation")
            ssl_context = False
        else:
            ssl_context = ssl.create_default_context()
        return pg8000.dbapi.connect(
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=unquote(parsed.path.lstrip("/")),
            ssl_context=ssl_context,
            timeout=30,
            application_name="finanzen-financial-records",
        )

    @contextmanager
    def connect(self):
        connection = self._open()
        try:
            yield DatabaseSession(connection, self.postgres)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
