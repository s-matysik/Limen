"""Public surface of the catalog app."""
from catalog.models import Product


def get_product(sku: str) -> Product:
    return Product(sku=sku, price_cents=1000)
