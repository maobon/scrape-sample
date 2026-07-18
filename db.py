import os
import json
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from config_loader import get_config_section

try:
    import psycopg2
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
            "host": parsed.hostname,
            "port": parsed.port or 5432,
            "dbname": parsed.path.lstrip("/"),
            "user": parsed.username,
            "password": parsed.password,
        }

    return {
        "host": os.getenv("PGHOST") or config.get("host", "localhost"),
        "port": int(os.getenv("PGPORT") or config.get("port", 5432)),
        "dbname": os.getenv("PGDATABASE") or config.get("database", "myapp"),
        "user": os.getenv("PGUSER") or config.get("user", "xinyi"),
        "password": os.getenv("PGPASSWORD") or config.get("password", ""),
    }


def get_connection():
    return psycopg2.connect(**_connection_kwargs())


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


def create_news_table():
    sql = """
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
        cursor.execute(sql)
        ensure_news_id_column(cursor)
        cursor.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS img TEXT;")


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


def clear_news_table():
    with db_cursor(commit=True) as cursor:
        cursor.execute("DROP TABLE IF EXISTS news CASCADE;")
        cursor.execute("DROP SEQUENCE IF EXISTS news_id_seq;")
    create_news_table()


def load_news_from_json(json_path="out.json"):
    path = Path(json_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("out.json 必须是新闻列表")

    return replace_news(data)


def replace_news(data):
    if not isinstance(data, list):
        raise ValueError("新闻数据必须是列表")

    create_news_table()

    sql = """
    INSERT INTO news (title, url, image, img, summary, date)
    VALUES %s
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
        cursor.execute("TRUNCATE TABLE news RESTART IDENTITY;")
        inserted = []
        if rows:
            inserted = execute_values(cursor, sql, rows, fetch=True)
        cursor.execute("SELECT COUNT(*) AS count FROM news;")
        result = cursor.fetchone()
        result["rows"] = inserted
        return result


if __name__ == "__main__":
    print(load_news_from_json())
