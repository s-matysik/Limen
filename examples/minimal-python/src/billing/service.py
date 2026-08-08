from core.api import load_invoices


def total_due() -> float:
    return sum(inv.total for inv in load_invoices())
