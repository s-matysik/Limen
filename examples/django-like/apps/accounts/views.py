"""Account views: own models and services only."""
from accounts.models import User


def profile_view(user_id: int) -> dict:
    user = User(user_id, "user@example.com")
    return {"id": user.user_id, "email": user.email}
