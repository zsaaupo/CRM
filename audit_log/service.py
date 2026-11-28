"""Audit log permissions, queries, serialization, and event recording."""
from contextvars import ContextVar
from dataclasses import dataclass

from django.apps import apps
from django.contrib.auth.models import Group
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db import connections, router, transaction
from django.db.models import F, OuterRef, Q, Subquery
from django.db.models.signals import m2m_changed, post_save, pre_delete
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from account.permissions import ACCESS_RULES
from accounting.services import (
    CHALAN_PAYMENT_VIEW, INCOME_PAYMENT_VIEW, PAYMENT_VIEW, PURCHASE_PAYMENT_VIEW, SALES_PAYMENT_VIEW,
    STATEMENT_VIEW, TRANSFER_VIEW,
)
from hrm.models import Employee
from hrm.permissions import NOTICE_BOARD_PERMISSIONS, PAYROLL_DETAIL, PAYROLL_PAYMENT_VIEW, REVIEW_LEAVE_PERMISSION
from inventory.models import RAW_MATERIAL_TYPE
from sale.permissions import DELIVERY_VIEW, PACKING_SLIP_VIEW
from warehouse.models import WareHouse
from warehouse.scope import all_warehouse_access, warehouse_scope
from .models import AuditLog


AUDIT_LOG_VIEW = ('audit_log.view_auditlog',)


@dataclass(frozen=True)
class Visibility:
    warehouses: tuple = ()
    permissions: tuple = ()
    both_warehouses: bool = False


POLICIES = dict.fromkeys('''
    auth.user auth.group common.productstatus common.tradestatus
    inventory.product inventory.productcategory inventory.brand inventory.unitofmeasure inventory.producttype inventory.currency
    people.party people.lead accounting.expensecategory accounting.incomecategory sale.courier
    hrm.department hrm.designation hrm.employmenttype hrm.employeestatus hrm.employee
    hrm.workschedule hrm.employeescheduleassignment hrm.holiday hrm.employeeattendancestatus hrm.attendance
    hrm.attendancepolicy hrm.leavetype hrm.leaverequest hrm.salarycomponent hrm.salarystructure hrm.salarystructureline
    hrm.employeecompensation hrm.payrollperiod hrm.payrollrun hrm.payrolladjustment
    hrm.payrolldeductionrule hrm.employeeloan hrm.loaninstallment
'''.split(), Visibility())
POLICIES.update(dict.fromkeys('''
    warehouse.stock warehouse.stockadjustment warehouse.stockcount purchase.purchaseorder sale.salesorder sale.salesquotation
    raw_material.rawmaterial sale.chalan
    accounting.expense accounting.income accounting.financialaccount
'''.split(), Visibility(('warehouse_id',))))
POLICIES.update({
    'account.account': Visibility(permissions=ACCESS_RULES['view_users']),
    'auth.group_permissions': Visibility(permissions=ACCESS_RULES['manage_role_permissions']),
    'auth.user_permissions': Visibility(permissions=ACCESS_RULES['manage_role_permissions'] + ACCESS_RULES['view_users']),
    'hrm.notice': Visibility(permissions=NOTICE_BOARD_PERMISSIONS),
    'hrm.leavebalance': Visibility(permissions=('hrm.view_leavebalance', 'hrm.view_all_leavebalance')),
    'hrm.leavebalancemovement': Visibility(permissions=('hrm.view_leavebalance', 'hrm.view_all_leavebalance')),
    'hrm.approvedleaveday': Visibility(permissions=('hrm.view_leaverequest', REVIEW_LEAVE_PERMISSION)),
    'hrm.payslip': Visibility(permissions=PAYROLL_DETAIL),
    'hrm.payslipline': Visibility(permissions=PAYROLL_DETAIL),
    'hrm.payrollevent': Visibility(permissions=PAYROLL_DETAIL),
    'hrm.salarypayment': Visibility(permissions=PAYROLL_PAYMENT_VIEW),
    'warehouse.warehouse': Visibility(('pk',)),
    'warehouse.warehousetransfer': Visibility(('source_warehouse_id', 'destination_warehouse_id')),
    'warehouse.warehousetransferitem': Visibility(('transfer.source_warehouse_id', 'transfer.destination_warehouse_id'),
                                                  ('warehouse.view_warehousetransfer',)),
    'warehouse.stockadjustmentitem': Visibility(('adjustment.warehouse_id',), ('warehouse.view_stockadjustment',)),
    'warehouse.rolewarehouseaccess': Visibility(('warehouse_id',), ('warehouse.manage_warehouse_access',)),
    'purchase.purchaseorderitem': Visibility(('purchase_order.warehouse_id',)),
    'purchase.purchasereturn': Visibility(('purchase_order.warehouse_id',)),
    'purchase.purchasereturnitem': Visibility(('purchase_return.purchase_order.warehouse_id',),
                                               ('purchase.view_purchasereturn', 'purchase.view_purchasereturnitem')),
    'sale.salesorderitem': Visibility(('sales_order.warehouse_id',)),
    'sale.chalanitem': Visibility(('chalan.warehouse_id',), ('sale.view_chalan', 'sale.view_chalanitem')),
    'sale.packingslip': Visibility(('chalan.warehouse_id',), PACKING_SLIP_VIEW),
    'sale.delivery': Visibility(('chalan.warehouse_id',), DELIVERY_VIEW),
    'sale.salesreturn': Visibility(('sales_order.warehouse_id',)),
    'sale.salesreturnitem': Visibility(('sales_return.sales_order.warehouse_id',),
                                       ('sale.view_salesreturn', 'sale.view_salesreturnitem')),
    'sale.salesquotationitem': Visibility(('quotation.warehouse_id',), ('sale.view_salesquotation', 'sale.view_salesquotationitem')),
    'accounting.accounttransfer': Visibility(('source_account.warehouse_id', 'destination_account.warehouse_id'), TRANSFER_VIEW, True),
    'accounting.accounttransaction': Visibility(('account.warehouse_id',), STATEMENT_VIEW),
    'accounting.expensepayment': Visibility(('account.warehouse_id', 'expense.warehouse_id'), PAYMENT_VIEW, True),
    'accounting.incomepayment': Visibility(('account.warehouse_id', 'income.warehouse_id'), INCOME_PAYMENT_VIEW, True),
    'accounting.salespayment': Visibility(('account.warehouse_id', 'sales_order.warehouse_id'), SALES_PAYMENT_VIEW, True),
    'accounting.chalanpayment': Visibility(('account.warehouse_id', 'chalan.warehouse_id'), CHALAN_PAYMENT_VIEW, True),
    'accounting.purchasepayment': Visibility(('account.warehouse_id', 'purchase_order.warehouse_id'), PURCHASE_PAYMENT_VIEW, True),
})


def visible_logs(queryset, user):
    if user.is_superuser:
        return queryset

    global_grants = user.get_all_permissions()
    try:
        scope = warehouse_scope(user)
    except PermissionDenied:
        scope = None
        role_grants = set()
    else:
        role_grants = {f'{app}.{code}' for app, code in scope.role.permissions.values_list('content_type__app_label', 'codename')}

    global_resources, branch_resources, shared_resources, both_resources = [], [], [], []
    restricted = Q(pk__in=[])
    for resource, policy in POLICIES.items():
        app, model = resource.split('.')
        permissions = policy.permissions or (f'{app}.view_{model}',)
        grants = role_grants if policy.warehouses else global_grants
        if not grants.issuperset(permissions):
            continue
        if resource == 'hrm.leaverequest' and REVIEW_LEAVE_PERMISSION not in global_grants:
            employees = Employee.objects.filter(user_id=user.pk).values('pk')
            restricted |= Q(resource=resource, subject_id__in=employees)
        elif resource == 'warehouse.rolewarehouseaccess':
            protected_roles = Group.objects.filter(user__is_superuser=True).values('pk')
            assignments = Q(resource=resource, protected_role=False) & ~Q(subject_id__in=protected_roles)
            if not all_warehouse_access(scope):
                assignments &= Q(all_warehouses=False) & (Q(warehouse_id=scope.warehouse_id) | Q(warehouse_id__isnull=True))
            restricted |= assignments
        elif not policy.warehouses:
            global_resources.append(resource)
        elif len(policy.warehouses) == 1:
            branch_resources.append(resource)
        elif policy.both_warehouses:
            both_resources.append(resource)
        else:
            shared_resources.append(resource)

    visible = Q(resource__in=global_resources) | restricted
    if all_warehouse_access(scope):
        visible |= Q(resource__in=branch_resources + shared_resources + both_resources)
    else:
        branch = Q(warehouse_id=scope.warehouse_id)
        related = Q(related_warehouse_id=scope.warehouse_id)
        visible |= (Q(resource__in=branch_resources) & branch)
        visible |= (Q(resource__in=shared_resources) & (branch | related))
        visible |= (Q(resource__in=both_resources) & branch & related)
    return queryset.filter(visible)


def audit_log_queryset():
    return AuditLog.objects.annotate(
        warehouse_name=Subquery(WareHouse.objects.filter(pk=OuterRef('warehouse_id')).values('name')),
        related_warehouse_name=Subquery(WareHouse.objects.filter(pk=OuterRef('related_warehouse_id')).values('name')),
    )


def filter_audit_logs(queryset, user, params):
    queryset = visible_logs(queryset, user)
    action = params.get('action', '').strip()
    if action:
        queryset = queryset.filter(action=action)
    record_type = params.get('record_type', '').strip()
    return queryset.filter(resource_label=record_type) if record_type else queryset


def visible_record_types(user):
    visible = visible_logs(AuditLog.objects.all(), user)
    return visible.order_by('resource_label').values_list('resource_label', flat=True).distinct()


class AuditLogSerializer(serializers.ModelSerializer):
    action_label = serializers.SerializerMethodField()
    warehouse_label = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = ['id', 'occurred_at', 'actor', 'resource', 'resource_label', 'object_id', 'reference',
                  'action', 'action_label', 'warehouse_id', 'related_warehouse_id', 'warehouse_label']
        read_only_fields = fields

    def get_action_label(self, event):
        return event.action.replace('_', ' ').capitalize()

    def get_warehouse_label(self, event):
        if event.all_warehouses:
            return 'All warehouses'
        warehouses = {}
        for key, name in [(event.warehouse_id, event.warehouse_name),
                          (event.related_warehouse_id, event.related_warehouse_name)]:
            if key is not None:
                warehouses[key] = name or f'Warehouse #{key}'
        return ' / '.join(warehouses.values()) or None


active_request = ContextVar('audit_log_request', default=None)
REFERENCE_FIELDS = ('order_number', 'return_number', 'quotation_number', 'adjustment_number',
                    'transfer_number', 'slip_number', 'chalan_number', 'sku', 'employee_code', 'code', 'number', 'reference_no')


def audit_request(get_response):
    def middleware(request):
        if request.method in {'GET', 'HEAD', 'OPTIONS', 'TRACE'}:
            return get_response(request)
        token = active_request.set(request)
        try:
            return get_response(request)
        finally:
            active_request.reset(token)
    return middleware


def actor_name(actor):
    if actor is not None:
        return actor if isinstance(actor, str) else actor.get_username()
    request = active_request.get()
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        return user.get_username()
    return ''


def audit_entry(instance, action, actor, resource=None):
    resource = resource or instance._meta.label_lower
    policy = POLICIES[resource]
    warehouses = []
    for path in policy.warehouses:
        value = instance
        for field in path.split('.'):
            value = getattr(value, field, None)
        warehouses.append(value)
    warehouses.extend([None] * (2 - len(warehouses)))

    reference = next((value for field in REFERENCE_FIELDS if (value := getattr(instance, field, None))), f'#{instance.pk}')
    subject = None
    restricted_role = False
    all_warehouses = False
    if resource == 'hrm.leaverequest':
        subject = instance.employee_id
    elif resource == 'warehouse.rolewarehouseaccess':
        subject = instance.role_id
        restricted_role = instance.role.user_set.filter(is_superuser=True).exists()
        all_warehouses = instance.all_warehouses
    elif resource == 'hrm.payrollevent':
        reference = f'Payroll run #{instance.run_id}'
    elif resource == 'sale.courier':
        reference = instance.name

    recorded_actor = actor_name(actor)
    if not recorded_actor and action in {'created', 'posted'}:
        recorded_actor = getattr(instance, 'create_by', None) or ''
    return AuditLog(
        resource=resource, resource_label=str(instance._meta.verbose_name).capitalize(),
        object_id=str(instance.pk), reference=str(reference)[:120], action=str(action)[:40],
        actor=recorded_actor, warehouse_id=warehouses[0], related_warehouse_id=warehouses[1],
        subject_id=subject, all_warehouses=all_warehouses, protected_role=restricted_role,
    )


def record_activity(instance, action, *, actor=None, using=None, resource=None):
    """Record a successful change in the same transaction as the affected record."""
    entry = audit_entry(instance, action, actor, resource)
    entry.save(using=using or instance._state.db or router.db_for_write(AuditLog))


def record_activities(instances, action, *, actor=None, using=None):
    AuditLog.objects.using(using).bulk_create([audit_entry(instance, action, actor) for instance in instances])


def bulk_create_with_audit(manager, records):
    with transaction.atomic(using=manager.db):
        records = manager.bulk_create(records)
        record_activities(records, 'created', using=manager.db)
    return records


def soft_delete_with_audit(queryset, flag, changes, action, actor):
    with transaction.atomic(using=queryset.db):
        if connections[queryset.db].vendor == 'sqlite':
            # Keep SQLite's first operation a write, as in the original bulk update.
            queryset.update(**{flag: F(flag)})
        records = list(queryset.select_for_update())
        count = queryset.update(**changes)
        record_activities(records, action, actor=actor, using=queryset.db)
    return count


def saved(sender, instance, created, raw, using, update_fields, **kwargs):
    if raw:
        return
    resource = sender._meta.label_lower
    if resource == 'inventory.product' and instance.product_type_id == RAW_MATERIAL_TYPE:
        return
    fields = set(update_fields or ())
    if fields and ((resource == 'auth.user' and fields <= {'last_login'}) or (
        resource == 'account.account' and fields <= {'otp', 'otp_expires_at', 'updated_at'}
    )):
        return
    actor = None if active_request.get() is not None or 'modified_by' not in fields else instance.modified_by
    action = 'created' if created else 'updated'
    if resource == 'hrm.payrollevent':
        actor, action = instance.actor, instance.action
    elif resource == 'hrm.notice' and created:
        actor = instance.created_by
    elif created and resource in {'accounting.accounttransfer', 'accounting.accounttransaction', 'accounting.expensepayment',
                                  'accounting.incomepayment', 'accounting.salespayment', 'accounting.purchasepayment',
                                  'accounting.chalanpayment'}:
        action = 'posted'
    elif 'is_completed' in fields and instance.is_completed:
        action = 'completed'
    elif 'status' in fields and resource in {'sale.salesquotation', 'sale.delivery', 'hrm.leaverequest'}:
        action = instance.status
    record_activity(instance, action, actor=actor, using=using)


def deleted(sender, instance, using, **kwargs):
    if sender._meta.label_lower == 'inventory.product' and instance.product_type_id == RAW_MATERIAL_TYPE:
        return
    record_activity(instance, 'deleted', using=using)


def logged_in(sender, user, **kwargs):
    if active_request.get() is not None:
        record_activity(user, 'signed_in', actor=user)


def logged_out(sender, user, **kwargs):
    if user is not None and user.is_authenticated and active_request.get() is not None:
        record_activity(user, 'signed_out', actor=user)


def memberships_changed(sender, instance, action, reverse, model, pk_set, using, **kwargs):
    if action not in {'post_add', 'post_remove', 'post_clear'} or (pk_set is not None and not pk_set):
        return
    change = {'post_add': 'added', 'post_remove': 'removed', 'post_clear': 'cleared'}[action]
    if sender._meta.model_name == 'user_groups':
        kind = 'members' if reverse else 'roles'
        record_activity(instance, f'{kind}_{change}', using=using)
    else:
        if reverse:
            for target in model._base_manager.using(using).filter(pk__in=pk_set or ()):
                record_activity(target, f'permissions_{change}', using=using, resource=f'{target._meta.label_lower}_permissions')
        else:
            record_activity(instance, f'permissions_{change}', using=using, resource=f'{instance._meta.label_lower}_permissions')


def connect_signals():
    for resource in POLICIES:
        if resource in {'auth.user_permissions', 'auth.group_permissions'}:
            continue
        model = apps.get_model(resource)
        post_save.connect(saved, sender=model, dispatch_uid=f'audit_log.save.{resource}')
        pre_delete.connect(deleted, sender=model, dispatch_uid=f'audit_log.delete.{resource}')
    user = apps.get_model('auth.User')
    group = apps.get_model('auth.Group')
    for through in (user.groups.through, user.user_permissions.through, group.permissions.through):
        m2m_changed.connect(memberships_changed, sender=through, dispatch_uid=f'audit_log.{through._meta.label_lower}')
    user_logged_in.connect(logged_in, dispatch_uid='audit_log.signed_in')
    user_logged_out.connect(logged_out, dispatch_uid='audit_log.signed_out')
