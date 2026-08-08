"""Infrastructure: the only place that talks to storage."""


def connect():
    return object()


def fetch_rows(table: str) -> list[dict]:
    return [{"table": table, "total": 10.0}]
