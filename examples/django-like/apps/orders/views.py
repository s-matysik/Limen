from orders.services import place_order


def checkout_view(user_id: int, sku: str) -> dict:
    order = place_order(user_id, sku)
    return {"order_id": order.order_id, "lines": order.lines}
