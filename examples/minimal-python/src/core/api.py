"""Public surface of the core unit."""
from core.domain.model import Invoice
from core.infra.db import fetch_rows


def load_invoices() -> list[Invoice]:
    return [Invoice(row) for row in fetch_rows("invoices")]


def get_connection():
    from core.infra.db import connect
    return connect()
