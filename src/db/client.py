import os
import json
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from src.utils.config import get_config_section

try:
    import psycopg2
    from psycopg2 import errorcodes
    from psycopg2 import sql
    from psycopg2.extras import RealDictCursor, execute_values
except ImportError as exc:
    raise RuntimeError(
        "缺少 PostgreSQL 驱动，请先安装：pip install psycopg2-binary"
    ) from exc

def _connection_kwargs():
    config = get_config_section("postgres")
    database_url = os.getenv("DATABASE_URL") or config.get("database_url")
    if database_url:
        parsed = urlparse(database_url)
        return {
            "host": parsed.hostname or os.getenv("PGHOST") or config.get("host", "localhost"),
            "port": parsed.port or int(os.getenv("PGPORT") or config.get("port", 5432)),
            "dbname": parsed.path.lstrip("/") or os.getenv("PGDATABASE") or config.get("database", "myapp"),
            "user": parsed.username or os.getenv("PGUSER") or config.get("user", "postgres"),
            "password": parsed.password if parsed.password is not None else os.getenv("PGPASSWORD") or config.get("password", ""),
        }

    return {
        "host": os.getenv("PGHOST") or config.get("host", "localhost"),
        "port": int(os.getenv("PGPORT") or config.get("port", 5432)),
        "dbname": os.getenv("PGDATABASE") or config.get("database", "myapp"),
        "user": os.getenv("PGUSER") or config.get("user", "postgres"),
        "password": os.getenv("PGPASSWORD") or config.get("password", ""),
    }

def _format_connection_target(kwargs):
    return (
        f"{kwargs.get('user') or '<default-user>'}@"
        f"{kwargs.get('host') or '<default-host>'}:"
        f"{kwargs.get('port') or 5432}/"
        f"{kwargs.get('dbname') or '<default-db>'}"
    )

def _quote_identifier(value):
    return '"' + str(value).replace('"', '""') + '"'

def _create_database_hint(database_name):
    return (
        "Run as a PostgreSQL admin if automatic creation is not permitted: "
        f"sudo -iu postgres psql -c 'CREATE DATABASE {_quote_identifier(database_name)};'"
    )

def _is_missing_database_error(exc):
    message = str(exc).lower()
    return (
        getattr(exc, "pgcode", None) == errorcodes.INVALID_CATALOG_NAME
        or ("database" in message and "does not exist" in message)
    )

def get_connection(auto_init=True):
    kwargs = _connection_kwargs()
    try:
        return psycopg2.connect(**kwargs)
    except psycopg2.OperationalError as exc:
        if auto_init and _is_missing_database_error(exc):
            ensure_database_exists()
            return get_connection(auto_init=False)

        target = _format_connection_target(kwargs)
        raise RuntimeError(
            "Cannot connect to PostgreSQL "
            f"({target}). Check config.json postgres settings or DATABASE_URL/PG* "
            "environment variables, and make sure the database exists. "
            f"Original error: {exc}"
        ) from exc

def _maintenance_database_names(target_database):
    config = get_config_section("postgres")
    configured_name = (
        os.getenv("PGMAINTENANCE_DATABASE")
        or config.get("maintenance_database")
        or "postgres"
    )
    names = [configured_name, "postgres", "template1"]
    unique_names = []
    for database_name in names:
        if database_name and database_name not in unique_names:
            unique_names.append(database_name)
    if target_database in unique_names and len(unique_names) > 1:
        unique_names.remove(target_database)
    return unique_names

def _connect_to_first_available_database(base_kwargs, database_names):
    last_error = None
    for database_name in database_names:
        kwargs = dict(base_kwargs)
        kwargs["dbname"] = database_name
        try:
            connection = psycopg2.connect(**kwargs)
        except psycopg2.OperationalError as exc:
            last_error = exc
            continue
        return connection, database_name

    raise RuntimeError(
        "Cannot connect to a PostgreSQL maintenance database. "
        f"Tried: {', '.join(database_names)}. Original error: {last_error}"
    )

def ensure_database_exists():
    target_kwargs = _connection_kwargs()
    target_database = target_kwargs.get("dbname")
    if not target_database:
        raise ValueError("PostgreSQL target database name is empty")

    connection, maintenance_database = _connect_to_first_available_database(
        target_kwargs,
        _maintenance_database_names(target_database),
    )
    connection.autocommit = True
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s;",
                (target_database,),
            )
            if cursor.fetchone():
                return {
                    "database": target_database,
                    "created": False,
                    "maintenance_database": maintenance_database,
                }

            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_database))
            )
            return {
                "database": target_database,
                "created": True,
                "maintenance_database": maintenance_database,
            }
    except psycopg2.Error as exc:
        raise RuntimeError(
            f"PostgreSQL database {target_database!r} does not exist and could not "
            "be created automatically. The configured PostgreSQL user must have "
            f"CREATEDB permission. {_create_database_hint(target_database)} "
            f"Original error: {exc}"
        ) from exc
    finally:
        connection.close()

def init_database_schema():
    database_result = ensure_database_exists()
    create_news_table(ensure_database=False)
    return database_result

@contextmanager
def db_cursor(commit=False):
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            yield cursor
        if commit:
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def test_connection():
    with db_cursor() as cursor:
        cursor.execute("SELECT version() AS version;")
        return cursor.fetchone()

def create_news_table(ensure_database=True):
    if ensure_database:
        ensure_database_exists()

    sql_stmt = """
    CREATE TABLE IF NOT EXISTS news (
        id SERIAL PRIMARY KEY,
        title TEXT NOT NULL,
        url TEXT NOT NULL UNIQUE,
        image TEXT,
        img TEXT,
        summary TEXT,
        date DATE
    );
    """
    with db_cursor(commit=True) as cursor:
        cursor.execute(sql_stmt)
        ensure_news_id_column(cursor)
        cursor.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS img TEXT;")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_date ON news (date);")

def ensure_news_id_column(cursor):
    cursor.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS id INTEGER;")
    cursor.execute("CREATE SEQUENCE IF NOT EXISTS news_id_seq;")
    cursor.execute("ALTER SEQUENCE news_id_seq OWNED BY news.id;")
    cursor.execute("ALTER TABLE news ALTER COLUMN id SET DEFAULT nextval('news_id_seq');")
    cursor.execute(
        """
        SELECT setval(
            'news_id_seq',
            GREATEST(COALESCE(MAX(id), 0), 1),
            COALESCE(MAX(id), 0) > 0
        )
        FROM news;
        """
    )
    cursor.execute("UPDATE news SET id = nextval('news_id_seq') WHERE id IS NULL;")
    cursor.execute("ALTER TABLE news ALTER COLUMN id SET NOT NULL;")
    cursor.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'news'::regclass
                  AND contype = 'p'
            ) THEN
                ALTER TABLE news ADD PRIMARY KEY (id);
            END IF;
        END $$;
        """
    )

def reset_news_id_sequence(cursor):
    cursor.execute("SELECT pg_get_serial_sequence('news', 'id') AS sequence_name;")
    row = cursor.fetchone()
    sequence_name = row["sequence_name"] if row else None

    if not sequence_name:
        cursor.execute("CREATE SEQUENCE IF NOT EXISTS news_id_seq;")
        cursor.execute("ALTER SEQUENCE news_id_seq OWNED BY news.id;")
        cursor.execute("ALTER TABLE news ALTER COLUMN id SET DEFAULT nextval('news_id_seq');")
        sequence_name = "news_id_seq"

    cursor.execute("SELECT setval(%s::regclass, 1, false);", (sequence_name,))

def compact_news_ids(cursor):
    cursor.execute(
        """
        WITH numbered AS (
            SELECT id, ROW_NUMBER() OVER (ORDER BY id) AS new_id
            FROM news
        )
        UPDATE news
        SET id = -numbered.new_id
        FROM numbered
        WHERE news.id = numbered.id;
        """
    )
    cursor.execute("UPDATE news SET id = -id WHERE id < 0;")
    cursor.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('news', 'id')::regclass,
            GREATEST(COALESCE(MAX(id), 0), 1),
            COALESCE(MAX(id), 0) > 0
        )
        FROM news;
        """
    )

def clear_news_table():
    create_news_table()
    with db_cursor(commit=True) as cursor:
        cursor.execute("TRUNCATE TABLE news RESTART IDENTITY;")
        reset_news_id_sequence(cursor)

def upsert_news(data):
    if not isinstance(data, list):
        raise ValueError("新闻数据必须是列表")

    create_news_table()

    sql_stmt = """
    INSERT INTO news (title, url, image, img, summary, date)
    VALUES %s
    ON CONFLICT (url) DO UPDATE SET
        title = EXCLUDED.title,
        image = EXCLUDED.image,
        img = CASE WHEN EXCLUDED.img <> '' THEN EXCLUDED.img ELSE news.img END,
        summary = EXCLUDED.summary,
        date = EXCLUDED.date
    RETURNING id, title, url;
    """
    rows = [
        (
            item.get("title"),
            item.get("url"),
            item.get("image"),
            item.get("img") or "",
            item.get("summary"),
            item.get("date"),
        )
        for item in data
    ]

    with db_cursor(commit=True) as cursor:
        inserted = []
        if rows:
            inserted = execute_values(cursor, sql_stmt, rows, fetch=True)
            compact_news_ids(cursor)
        cursor.execute("SELECT COUNT(*) AS count FROM news;")
        result = cursor.fetchone()
        result["rows"] = inserted
        return result
