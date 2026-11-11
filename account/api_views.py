from datetime import timedelta
from functools import partial

from django.contrib.auth import authenticate, get_user_model, login as auth_login, logout as auth_logout
from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import GenericAPIView, ListAPIView, ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.authentication import SessionAuthentication
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .emails import thread_send_email
from .models import Account
from .permissions import ACCESS_RULES, HasRequiredPermissions, check_assignment_target, check_role_permission_target
from .serializers import (
    AccountSerializer,
    AddUserSerializer,
    AssignGroupSerializer,
    GroupDetailSerializer,
    GroupListSerializer,
    GroupPermissionsSerializer,
    LoginSerializer,
    PermissionListSerializer,
    ResendOTPSerializer,
    SignUpSerializer,
    UserListSerializer,
    UserGroupsSerializer,
    VerifyOTPSerializer,
)
from .utils import generate_otp, otp_matches


User = get_user_model()


@method_decorator(never_cache, name='dispatch')
@method_decorator(csrf_protect, name='dispatch')
class LoginAPIView(GenericAPIView):
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        return Response({'csrfToken': get_token(request)})

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = authenticate(
            request,
            username=data['email'],
            password=data['password'],
        )
        if user is None or not user.is_active:
            return Response(
                {'detail': 'Invalid email or password.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        auth_login(request, user)
        account = Account.objects.filter(user=user).first()
        return Response(
            {
                'message': 'Logged in successfully.',
                'user': {'id': user.pk, 'email': data['email']},
                'account': AccountSerializer(account).data if account else None,
                # Django rotates the CSRF secret on login; use this for later POSTs.
                'csrfToken': get_token(request),
            },
            status=status.HTTP_200_OK,
        )


@method_decorator(never_cache, name='dispatch')
@method_decorator(csrf_protect, name='dispatch')
class LogoutAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        auth_logout(request)
        return Response({'message': 'Logged out successfully.'}, status=status.HTTP_200_OK)


@method_decorator(never_cache, name='dispatch')
class AddUserAPIView(GenericAPIView):
    serializer_class = AddUserSerializer
    parser_classes = [JSONParser, FormParser, MultiPartParser]
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, HasRequiredPermissions]
    required_permissions = ACCESS_RULES['add_user']

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)


        data = dict(serializer.validated_data)
        password = data.pop('password')
        data.pop('password_confirm')
        email = data['email']

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=email,
                    email=email,
                    password=password,
                    is_active=True,
                )
                account = Account.objects.create(user=user, **data)
        except IntegrityError as exc:
            if User.objects.filter(username__iexact=email).exists():
                raise ValidationError({
                    'email': 'An account with this email already exists.',
                }) from exc
            raise

        return Response(
            {
                'message': 'User created successfully.',
                'account': AccountSerializer(account).data,
            },
            status=status.HTTP_201_CREATED,
        )


class UserListPagination(PageNumberPagination):
    page_size = 20


@method_decorator(never_cache, name='dispatch')
class UserListAPIView(ListAPIView):
    serializer_class = UserListSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, HasRequiredPermissions]
    required_permissions = ACCESS_RULES['view_users']
    pagination_class = UserListPagination

    def get_queryset(self):
        users = User.objects.select_related('account').order_by('-date_joined', '-pk')
        query = self.request.query_params.get('q', '').strip()
        if query:
            users = users.filter(
                Q(username__icontains=query)
                | Q(email__icontains=query)
                | Q(account__full_name__icontains=query)
                | Q(account__email__icontains=query)
                | Q(account__phone_number__icontains=query)
                | Q(account__designation__icontains=query)
            )
        return users


@method_decorator(never_cache, name='dispatch')
class PermissionListAPIView(ListAPIView):
    serializer_class = PermissionListSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, HasRequiredPermissions]
    required_permissions = ACCESS_RULES['view_permissions']
    pagination_class = None
    queryset = Permission.objects.select_related('content_type').order_by(
        'content_type__app_label', 'content_type__model', 'codename', 'pk',
    )


@method_decorator(never_cache, name='dispatch')
class GroupListAPIView(ListCreateAPIView):
    serializer_class = GroupListSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, HasRequiredPermissions]
    required_permissions = {'GET': ACCESS_RULES['view_roles'], 'POST': ACCESS_RULES['add_role']}
    pagination_class = None
    queryset = Group.objects.prefetch_related('permissions').order_by('name', 'pk')

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return GroupDetailSerializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        try:
            with transaction.atomic():
                serializer.save()
        except IntegrityError as exc:
            if Group.objects.filter(name=serializer.validated_data['name']).exists():
                raise ValidationError({'name': 'A group with this name already exists.'}) from exc
            raise


@method_decorator(never_cache, name='dispatch')
class GroupDetailAPIView(RetrieveUpdateDestroyAPIView):
    serializer_class = GroupDetailSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, HasRequiredPermissions]
    required_permissions = {
        'GET': ACCESS_RULES['view_roles'],
        'PATCH': ACCESS_RULES['change_role'],
        'DELETE': ACCESS_RULES['delete_role'],
    }
    http_method_names = ['get', 'patch', 'delete', 'head', 'options']
    queryset = Group.objects.prefetch_related('permissions')

    def perform_update(self, serializer):
        try:
            with transaction.atomic():
                serializer.save()
        except IntegrityError as exc:
            if Group.objects.filter(name=serializer.validated_data.get('name')).exclude(
                pk=serializer.instance.pk,
            ).exists():
                raise ValidationError({'name': 'A group with this name already exists.'}) from exc
            raise


@method_decorator(never_cache, name='dispatch')
class GroupPermissionsAPIView(GenericAPIView):
    serializer_class = GroupPermissionsSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, HasRequiredPermissions]
    required_permissions = ACCESS_RULES['manage_role_permissions']
    http_method_names = ['put', 'options']
    queryset = Group.objects.select_for_update()

    def put(self, request, *args, **kwargs):
        with transaction.atomic():
            group = self.get_object()
            check_role_permission_target(request.user, group)
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            group.permissions.set(serializer.validated_data['permissions'])
            result = GroupListSerializer(group).data

        return Response(result, status=status.HTTP_200_OK)


@method_decorator(never_cache, name='dispatch')
class UserGroupsAPIView(GenericAPIView):
    serializer_class = UserGroupsSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, HasRequiredPermissions]
    required_permissions = {'GET': ACCESS_RULES['view_users'], 'PUT': ACCESS_RULES['assign_role']}
    http_method_names = ['get', 'put', 'head', 'options']
    queryset = User.objects.all()

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.method in ('PUT', 'POST'):
            return queryset.select_for_update()
        return queryset

    def get(self, request, *args, **kwargs):
        user = self.get_object()
        return Response({
            'user_id': user.pk,
            'groups': list(user.groups.order_by('pk').values_list('pk', flat=True)),
        })

    def put(self, request, *args, **kwargs):
        with transaction.atomic():
            user = self.get_object()
            check_assignment_target(request.user, user)
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            group = serializer.validated_data['group']
            user.groups.set([group])
            result = {
                'user': user.pk,
                'group': group.pk,
            }
        return Response(result, status=status.HTTP_200_OK)


@method_decorator(never_cache, name='dispatch')
class AssignGroupAPIView(UserGroupsAPIView):
    serializer_class = AssignGroupSerializer
    http_method_names = ['post', 'options']
    required_permissions = ACCESS_RULES['assign_role']

    def post(self, request, *args, **kwargs):
        with transaction.atomic():
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            user = get_object_or_404(self.get_queryset(), pk=serializer.validated_data['user'])
            check_assignment_target(request.user, user)
            group = serializer.validated_data['group']
            user.groups.set([group])
        return Response({'user': user.pk, 'group': group.pk}, status=status.HTTP_200_OK)


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
