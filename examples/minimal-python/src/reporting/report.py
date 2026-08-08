from core.api import load_invoices


def render() -> str:
    return "\n".join(f"invoice: {inv.total:.2f}" for inv in load_invoices())
