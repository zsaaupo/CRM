from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework import serializers

from .models import Account
from .strings import gender_choice


User = get_user_model()


class AccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = Account
        fields = [
            'id',
            'user',
            'full_name',
            'email',
            'date_of_birth',
            'gender',
            'phone_number',
            'designation',
            'address',
            'city',
            'state',
            'postal_code',
            'country',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class SignUpSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150)
    email = serializers.EmailField(max_length=150)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    password_confirm = serializers.CharField(write_only=True, trim_whitespace=False)
    phone_number = serializers.CharField(max_length=32, required=False, allow_blank=True)

    @staticmethod
    def email_exists(email):
        return User.objects.filter(Q(username__iexact=email)).exists()

    def validate_email(self, value):
        email = value.lower()
        if self.email_exists(email):
            raise serializers.ValidationError('An account with this email already exists.')
        return email

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({
                'password_confirm': 'Passwords do not match.',
            })

        email = attrs['email']
        candidate_user = User(username=email)
        try:
            validate_password(attrs['password'], user=candidate_user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': exc.messages}) from exc
        return attrs


class VerifyOTPSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    otp = serializers.RegexField(
        regex=r'\A[0-9A-Z]{6}\Z',
        min_length=6,
        max_length=6,
        write_only=True,
        error_messages={
            'invalid': 'Enter a six-character code using 0-9 and A-Z.',
        },
    )

    def validate_email(self, value):
        return value.lower()


class ResendOTPSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)

    def validate_email(self, value):
        return value.lower()
