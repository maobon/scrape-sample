from src.db.client import _connection_kwargs, init_database_schema

def _safe_connection_target():
    kwargs = _connection_kwargs()
    return {
        key: value
        for key, value in kwargs.items()
        if key != "password"
    }

def main():
    print(f"PostgreSQL target: {_safe_connection_target()}")
    result = init_database_schema()
    action = "created" if result["created"] else "already exists"
    print(
        "PostgreSQL database "
        f"{result['database']}: {action} "
        f"(maintenance database: {result['maintenance_database']})"
    )
    print("PostgreSQL table news: ready")

if __name__ == "__main__":
    main()
