"""Public surface of the accounts app."""
from accounts.models import User


def get_user(user_id: int) -> User:
    return User(user_id=user_id, email="user@example.com")
