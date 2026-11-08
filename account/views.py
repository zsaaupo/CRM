from datetime import timedelta
from functools import partial

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .emails import thread_send_email
from .models import Account
from .serializers import (
    AccountSerializer,
    ResendOTPSerializer,
    SignUpSerializer,
    VerifyOTPSerializer,
)
from .utils import generate_otp, otp_matches


User = get_user_model()


class SignUpAPIView(GenericAPIView):
    serializer_class = SignUpSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        email = data['email']

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=email,
                    password=data['password'],
                    is_active=False,
                )
                otp = generate_otp()
                account = Account.objects.create(
                    user=user,
                    full_name=data['full_name'],
                    email=email,
                    phone_number=data.get('phone_number', ''),
                    otp=otp,
                    otp_expires_at=timezone.now() + timedelta(minutes=10),
                )
                transaction.on_commit(partial(
                    thread_send_email,
                    email,
                    'Verify your eManager account',
                    f'Your verification code is: {otp}\n\n'
                    'This code expires in 10 minutes.\n'
                    'If you did not request this signup, you can ignore this email.',
                ))
        except IntegrityError as exc:
            if User.objects.filter(username=email).exists():
                raise ValidationError({
                    'email': 'An account with this email already exists.',
                }) from exc
            raise

        return Response(
            {
                'message': 'Account created. Please check your email for a verification code.',
                'account': AccountSerializer(account).data,
            },
            status=status.HTTP_201_CREATED,
        )


class VerifyOTPAPIView(GenericAPIView):
    serializer_class = VerifyOTPSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        verified = False

        with transaction.atomic():
            account = (
                Account.objects.select_for_update()
                .select_related('user')
                .filter(user__username=data['email'], email__iexact=data['email'])
                .first()
            )
            now = timezone.now()
            if (
                account is not None
                and not account.user.is_active
                and account.otp
                and account.otp_expires_at is not None
                and account.otp_expires_at > now
            ):
                pending_code = Account.objects.filter(
                    pk=account.pk,
                    otp=account.otp,
                    otp_expires_at__gt=now,
                )
                if otp_matches(data['otp'], account.otp):
                    consumed = pending_code.update(
                        otp=None,
                        otp_expires_at=None,
                        updated_at=now,
                    )
                    if consumed:
                        account.user.is_active = True
                        account.user.save(update_fields=['is_active'])
                        verified = True

        if not verified:
            raise ValidationError({'otp': 'Invalid or expired verification code.'})

        return Response(
            {'message': 'Email verified successfully. You can now log in.'},
            status=status.HTTP_200_OK,
        )


class ResendOTPAPIView(GenericAPIView):
    serializer_class = ResendOTPSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        with transaction.atomic():
            account = (
                Account.objects.select_for_update()
                .select_related('user')
                .filter(
                    user__username=email,
                    email__iexact=email,
                    user__is_active=False,
                )
                .first()
            )
            if account is None:
                raise ValidationError({
                    'email': 'No pending verification found for this email.',
                })

            otp = generate_otp()
            while otp_matches(otp, account.otp):
                otp = generate_otp()

            account.otp = otp
            account.otp_expires_at = timezone.now() + timedelta(minutes=10)
            account.save(update_fields=['otp', 'otp_expires_at', 'updated_at'])

            transaction.on_commit(partial(
                thread_send_email,
                account.email,
                'Verify your eManager account',
                f'Your new verification code is: {otp}\n\n'
                'This code expires in 10 minutes.\n'
                'If you did not request this code, you can ignore this email.',
            ))

        return Response(
            {'message': 'A new verification code has been generated. Please check your email.'},
            status=status.HTTP_200_OK,
        )
