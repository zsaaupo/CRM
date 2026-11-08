import secrets
import string

from django.contrib.auth.hashers import check_password, identify_hasher
from django.utils.crypto import constant_time_compare


def generate_otp():
    """Return a secure six-character code with at least one digit and one letter."""
    alphabet = string.digits + string.ascii_uppercase
    while True:
        otp = ''.join(secrets.choice(alphabet) for _ in range(6))
        if any(char.isdigit() for char in otp) and any(char.isalpha() for char in otp):
            return otp


def otp_matches(submitted_code, stored_code):
    """Accept current plain-text OTPs and previously issued Django-hashed OTPs."""
    if not stored_code:
        return False
    try:
        identify_hasher(stored_code)
    except ValueError:
        return constant_time_compare(submitted_code, stored_code)
    return check_password(submitted_code, stored_code)
