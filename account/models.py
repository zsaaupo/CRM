from django.conf import settings
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from .strings import gender_choice


class Account(models.Model):

    class Meta:
        permissions = [('manage_access', 'Can manage role assignments and role permissions')]

    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name='account')

    full_name = models.CharField(max_length=150)
    email = models.EmailField(max_length=254)
    otp = models.CharField(max_length=128, null=True, blank=True,editable=False)
    otp_expires_at = models.DateTimeField(null=True, blank=True, editable=False)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=20, choices=gender_choice, null=True, blank=True)

    phone_number = models.CharField(max_length=32)
    designation = models.CharField(max_length=150, null=True, blank=True)

    address = models.TextField(null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    state = models.CharField(max_length=100, null=True, blank=True)
    postal_code = models.CharField(max_length=20, null=True, blank=True)
    country = models.CharField(max_length=100, null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.full_name or self.user.get_username()
