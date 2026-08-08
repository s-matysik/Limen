"""User model (stand-in for django.db models)."""


class User:
    def __init__(self, user_id: int, email: str):
        self.user_id = user_id
        self.email = email
