from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView

from .models import Account
from .permissions import ACCESS_RULES, access_flags, check_assignment_target, check_role_permission_target


@method_decorator(never_cache, name='dispatch')
class LoginPageView(TemplateView):
    template_name = 'account/login.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('dashboard')
        return super().dispatch(request, *args, **kwargs)


@method_decorator(never_cache, name='dispatch')
class ProtectedAccountPageView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    # Successful login always goes to the dashboard.
    redirect_field_name = None
    permission_required = ()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        account = Account.objects.filter(user=user).first()
        display_name = (
            (account.full_name if account else '')
            or user.get_full_name()
            or user.get_username()
        ).strip()
        words = display_name.split()
        access = access_flags(user)
        context.update({
            'profile': account,
            'display_name': display_name,
            'first_name': words[0] if words and '@' not in display_name else 'there',
            'user_initials': ''.join(word[0] for word in words[:2]).upper() or 'U',
            'profile_email': (account.email if account else '') or user.email or user.get_username(),
            'today': timezone.localdate(),
            'access': access,
            'user_list_url': reverse('account_pages:user-list') if access['view_users'] else reverse('dashboard'),
            'role_list_url': reverse('account_pages:role-list') if access['view_roles'] else reverse('dashboard'),
        })
        return context


class DashboardPageView(ProtectedAccountPageView):
    template_name = 'account/dashboard.html'
    extra_context = {'page_label': 'Dashboard', 'page_section': 'dashboard'}


class ProfilePageView(ProtectedAccountPageView):
    template_name = 'account/profile.html'
    extra_context = {'page_label': 'Profile', 'page_section': 'profile'}


class AddUserPageView(ProtectedAccountPageView):
    permission_required = ACCESS_RULES['add_user']
    template_name = 'account/add_user.html'
    extra_context = {'page_label': 'Add user', 'page_section': 'users', 'user_page': 'add'}


class UserListPageView(ProtectedAccountPageView):
    permission_required = ACCESS_RULES['view_users']
    template_name = 'account/user_list.html'
    extra_context = {'page_label': 'User list', 'page_section': 'users', 'user_page': 'list'}


class AssignRolePageView(ProtectedAccountPageView):
    permission_required = ACCESS_RULES['assign_role']
    template_name = 'account/assign_role.html'
    extra_context = {'page_label': 'Assign role', 'page_section': 'users'}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        target = get_object_or_404(
            get_user_model().objects.select_related('account'), pk=self.kwargs['pk'],
        )
        account = getattr(target, 'account', None)
        check_assignment_target(self.request.user, target)
        current_groups = list(target.groups.order_by('name', 'pk'))
        context.update({
            'target_user': target,
            'target_name': (account.full_name if account else '') or target.get_full_name() or target.get_username(),
            'target_email': (account.email if account else '') or target.email or target.get_username(),
            'roles': list(Group.objects.order_by('name', 'pk')),
            'selected_group_id': current_groups[0].pk if len(current_groups) == 1 else None,
            'current_groups': current_groups,
        })
        return context


class AddRolePageView(ProtectedAccountPageView):
    permission_required = ACCESS_RULES['add_role']
    template_name = 'account/add_role.html'
    extra_context = {'page_label': 'Add role', 'page_section': 'roles', 'role_page': 'add'}


class RoleListPageView(ProtectedAccountPageView):
    permission_required = ACCESS_RULES['view_roles']
    template_name = 'account/role_list.html'
    extra_context = {'page_label': 'Role list', 'page_section': 'roles', 'role_page': 'list'}


class RolePermissionsPageView(ProtectedAccountPageView):
    permission_required = ACCESS_RULES['manage_role_permissions']
    template_name = 'account/role_permissions.html'
    extra_context = {'page_label': 'Manage permissions', 'page_section': 'roles'}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['role'] = get_object_or_404(Group, pk=self.kwargs['pk'])
        check_role_permission_target(self.request.user, context['role'])
        return context


class SignUpPageView(TemplateView):
    template_name = 'account/signup.html'


class VerifyOTPPageView(TemplateView):
    template_name = 'account/verify_otp.html'
