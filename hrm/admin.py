from django.contrib import admin
from django import forms

from .models import Department, Designation, Employee, EmployeeStatus, EmploymentType
from .permissions import check_employee_user_link


@admin.register(Department, Designation, EmploymentType, EmployeeStatus)
class HRMLookupAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'is_active']
    search_fields = ['code', 'name', 'description']
    list_filter = ['is_active']
    readonly_fields = ['created_at', 'updated_at', 'create_by', 'modified_by']

    def get_readonly_fields(self, request, obj=None):
        return [*self.readonly_fields, *(['code'] if obj else [])]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.create_by = request.user.get_username()
        obj.modified_by = request.user.get_username()
        super().save_model(request, obj, form, change)


class EmployeeAdminForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = '__all__'

    def clean_user(self):
        user = self.cleaned_data.get('user')
        check_employee_user_link(self.actor, self.instance, user)
        return user


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    form = EmployeeAdminForm
    list_display = ['employee_code', 'full_name', 'department', 'designation', 'status', 'warehouse',
                    'is_archived', 'archived_at', 'archived_by']
    search_fields = ['employee_code', 'full_name', 'work_email', 'work_phone']
    list_filter = ['is_archived', 'status', 'employment_type', 'department', 'warehouse']
    list_select_related = ['department', 'designation', 'status', 'warehouse']
    readonly_fields = ['created_at', 'updated_at', 'create_by', 'modified_by',
                       'is_archived', 'archived_at', 'archived_by']

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # A fresh form class is produced per request; do not put actors on the shared base form.
        form.actor = request.user
        return form

    def get_readonly_fields(self, request, obj=None):
        if not request.user.has_perm('auth.change_user'):
            return [*self.readonly_fields, 'user']
        return self.readonly_fields

    def get_queryset(self, request):
        queryset = self.model.all_objects.all()
        ordering = self.get_ordering(request)
        return queryset.order_by(*ordering) if ordering else queryset

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_archived:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_archived:
            return False
        return super().has_delete_permission(request, obj)

    def get_deleted_objects(self, objs, request):
        employees = [obj for obj in objs if not obj.is_archived]
        permissions = set()
        if any(not self.has_delete_permission(request, obj) for obj in employees):
            permissions.add(str(self.opts.verbose_name))
        return ([str(obj) for obj in employees], {str(self.opts.verbose_name_plural): len(employees)}, permissions, [])

    def save_model(self, request, obj, form, change):
        if not change:
            obj.create_by = request.user.get_username()
        obj.modified_by = request.user.get_username()
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        obj.delete(actor=request.user.get_username())

    def delete_queryset(self, request, queryset):
        queryset.delete(actor=request.user.get_username())
