"""Order workflows: other apps only through their api modules."""
from accounts.api import get_user
from catalog.api import get_product
from orders.models import Order


def place_order(user_id: int, sku: str) -> Order:
    user = get_user(user_id)
    product = get_product(sku)
    order = Order(order_id=1)
    order.lines.append((user.user_id, product.sku))
    return order
