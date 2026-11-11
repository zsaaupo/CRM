from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
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
            'image',
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


class AddUserSerializer(SignUpSerializer):
    image = serializers.ImageField(required=False, allow_null=True)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    gender = serializers.ChoiceField(
        choices=gender_choice, required=False, allow_blank=True, allow_null=True,
    )
    designation = serializers.CharField(max_length=150, required=False, allow_blank=True, allow_null=True)
    address = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    state = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    postal_code = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    country = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)


class UserListSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    email = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()
    phone_number = serializers.SerializerMethodField()
    designation = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'full_name', 'email', 'image', 'phone_number', 'designation', 'is_active', 'date_joined']
        read_only_fields = fields

    def get_full_name(self, user):
        account = getattr(user, 'account', None)
        return (account.full_name if account else '') or user.get_full_name() or user.get_username()

    def get_email(self, user):
        account = getattr(user, 'account', None)
        return (account.email if account else '') or user.email or user.get_username()

    def get_image(self, user):
        account = getattr(user, 'account', None)
        return account.image.url if account and account.image else None

    def get_phone_number(self, user):
        account = getattr(user, 'account', None)
        return (account.phone_number if account else '') or ''

    def get_designation(self, user):
        account = getattr(user, 'account', None)
        return (account.designation if account else '') or ''


class PermissionListSerializer(serializers.ModelSerializer):
    app_label = serializers.CharField(source='content_type.app_label', read_only=True)
    model = serializers.CharField(source='content_type.model', read_only=True)

    class Meta:
        model = Permission
        fields = ['id', 'name', 'codename', 'content_type', 'app_label', 'model']
        read_only_fields = fields


class GroupListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = ['id', 'name', 'permissions']
        read_only_fields = fields


class GroupDetailSerializer(GroupListSerializer):
    class Meta(GroupListSerializer.Meta):
        read_only_fields = ['id', 'permissions']


class GroupPermissionsSerializer(serializers.Serializer):
    permissions = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
    )

    def validate_permissions(self, value):
        permission_ids = set(value)
        existing_ids = set(Permission.objects.filter(
            pk__in=permission_ids,
        ).values_list('pk', flat=True))
        invalid_ids = sorted(permission_ids - existing_ids)
        if invalid_ids:
            raise serializers.ValidationError(
                f'Invalid permission IDs: {", ".join(map(str, invalid_ids))}.',
            )
        return sorted(permission_ids)


class UserGroupsSerializer(serializers.Serializer):
    group = serializers.IntegerField(min_value=1)

    def validate_group(self, value):
        try:
            return Group.objects.get(pk=value)
        except Group.DoesNotExist as exc:
            raise serializers.ValidationError('Invalid group ID.') from exc

    def validate(self, attrs):
        if 'groups' in self.initial_data:
            raise serializers.ValidationError({'groups': 'Provide one group ID using "group".'})
        return attrs


class AssignGroupSerializer(UserGroupsSerializer):
    user = serializers.IntegerField(min_value=1)


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


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=150)
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_email(self, value):
        return value.lower()
