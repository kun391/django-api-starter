from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction

User = get_user_model()


@transaction.atomic
def register_user(*, email, username, password, first_name="", last_name=""):
    """Create a user while enforcing Django's configured password policy."""
    user = User(
        email=email,
        username=username,
        first_name=first_name,
        last_name=last_name,
    )
    validate_password(password, user=user)
    user.set_password(password)
    user.save()
    return user
