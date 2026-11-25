from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    create_by = models.CharField(max_length=255, null=True)
    modified_by = models.CharField(max_length=255, null=True)

    class Meta:
        abstract = True


class Status(TimeStampedModel):
    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ['name', 'pk']

    def __str__(self):
        return self.name


class ProductStatus(Status):
    class Meta(Status.Meta):
        verbose_name = 'product status'
        verbose_name_plural = 'product statuses'


class TradeStatus(Status):
    class Meta(Status.Meta):
        verbose_name = 'trade status'
        verbose_name_plural = 'trade statuses'


class PaymentStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    DUE = 'due', 'Due'
    PARTIAL = 'partial', 'Partial'
    PAID = 'paid', 'Paid'


class SettlementMode(models.TextChoices):
    AUTO_FULL = 'auto_full', 'Auto full'
    DEFERRED = 'deferred', 'Deferred'
    EXPLICIT = 'explicit', 'Explicit'


class DiscountType(models.TextChoices):
    FLAT = 'flat', 'Flat'
    PERCENTAGE = 'percentage', 'Percentage'


class TaxMethod(models.TextChoices):
    EXCLUSIVE = 'exclusive', 'Exclusive'
    INCLUSIVE = 'inclusive', 'Inclusive'


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError(f'{self.model.__name__} is append-only and cannot be updated.')

    def delete(self):
        raise ValueError(f'{self.model.__name__} is append-only and cannot be deleted.')


class AppendOnlyManager(models.Manager.from_queryset(AppendOnlyQuerySet)):
    pass


class GeneralSetting(TimeStampedModel):
    class StaffAccess(models.TextChoices):
        ALL = 'all', 'All'
        OWN = 'own', 'Own'
        WAREHOUSE = 'warehouse', 'Warehouse'

    class CurrencyPosition(models.TextChoices):
        PREFIX = 'prefix', 'Prefix'
        SUFFIX = 'suffix', 'Suffix'

    class InvoiceFormat(models.TextChoices):
        STANDARD = 'standard', 'Standard'
        THERMAL = 'thermal', 'Thermal'
        GST = 'gst', 'GST'

    class ExpiryDurationType(models.TextChoices):
        DAYS = 'days', 'Days'
        MONTHS = 'months', 'Months'

    # Identity
    system_title = models.CharField(max_length=150, default='eManager ERP')
    system_logo = models.ImageField(upload_to='settings/', blank=True)
    rtl_layout = models.BooleanField(default=False)
    company_name = models.CharField(max_length=200, default='eManager')
    vat_registration_number = models.CharField(max_length=100, blank=True)
    timezone = models.CharField(max_length=64, default='Asia/Dhaka')
    developed_by = models.CharField(max_length=150, blank=True, default='eManager')

    # Operations
    allow_sale_without_stock = models.BooleanField(default=False)
    enable_packing_slip = models.BooleanField(default=False)
    enable_in_house_production = models.BooleanField(default=True)
    staff_access = models.CharField(
        max_length=16, choices=StaffAccess.choices, default=StaffAccess.WAREHOUSE,
    )
    costing_method = models.CharField(max_length=16, default='wavg')
    restore_coupon_on_full_return = models.BooleanField(default=False)
    block_expired_batch_sale = models.BooleanField(default=True)

    # Formatting
    default_currency = models.ForeignKey(
        'inventory.Currency', to_field='code', on_delete=models.PROTECT,
        null=True, blank=True, related_name='general_settings',
    )
    default_customer_group = models.ForeignKey(
        'people.CustomerGroup', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    currency_position = models.CharField(
        max_length=10, choices=CurrencyPosition.choices, default=CurrencyPosition.PREFIX,
    )
    decimal_digits = models.PositiveSmallIntegerField(default=2)
    invoice_format = models.CharField(
        max_length=16, choices=InvoiceFormat.choices, default=InvoiceFormat.STANDARD,
    )
    date_format = models.CharField(max_length=32, default='d M Y')
    expiry_duration_type = models.CharField(
        max_length=10, choices=ExpiryDurationType.choices, default=ExpiryDurationType.DAYS,
    )
    expiry_value = models.PositiveIntegerField(default=30)

    class Meta:
        verbose_name = 'general setting'
        verbose_name_plural = 'general settings'

    def clean(self):
        super().clean()
        if self.decimal_digits > 6:
            raise ValidationError({'decimal_digits': 'Decimal digits cannot exceed 6.'})
        if self.costing_method != 'wavg':
            raise ValidationError({'costing_method': 'Only weighted average (wavg) costing is supported.'})

    def save(self, *args, **kwargs):
        self.pk = 1
        if 'update_fields' in kwargs and not GeneralSetting.objects.filter(pk=1).exists():
            kwargs.pop('update_fields')
        super().save(*args, **kwargs)
        from .settings_store import invalidate_general_cache
        invalidate_general_cache()

    def delete(self, *args, **kwargs):
        raise ValueError('GeneralSetting singleton cannot be deleted.')

    def __str__(self):
        return self.system_title


class IdempotencyRecord(models.Model):
    key = models.UUIDField(unique=True)
    scope = models.CharField(max_length=64, db_index=True)
    payload_hash = models.CharField(max_length=64)
    result_type = models.CharField(max_length=64)
    result_id = models.BigIntegerField()
    result_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [
            models.Index(fields=['scope', 'created_at'], name='common_idem_scope_created_idx'),
        ]

    def __str__(self):
        return f'{self.scope}:{self.key}'


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE = 'create', 'Create'
        UPDATE = 'update', 'Update'
        DELETE = 'delete', 'Delete'
        COMPLETE = 'complete', 'Complete'
        RECEIVE = 'receive', 'Receive'
        POST = 'post', 'Post'
        APPROVE = 'approve', 'Approve'
        PAYMENT = 'payment', 'Payment'
        REFUND = 'refund', 'Refund'
        LOGIN = 'login', 'Login'
        EXPORT = 'export', 'Export'
        SETTINGS_CHANGE = 'settings_change', 'Settings change'
        IMPORT = 'import', 'Import'
        CLOSE = 'close', 'Close'

    class Module(models.TextChoices):
        SALE = 'sale', 'Sale'
        PURCHASE = 'purchase', 'Purchase'
        WAREHOUSE = 'warehouse', 'Warehouse'
        INVENTORY = 'inventory', 'Inventory'
        ACCOUNTING = 'accounting', 'Accounting'
        HRM = 'hrm', 'HRM'
        MANUFACTURING = 'manufacturing', 'Manufacturing'
        TASKS = 'tasks', 'Tasks'
        PEOPLE = 'people', 'People'
        ACCOUNT = 'account', 'Account'
        SETTINGS = 'settings', 'Settings'

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs',
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    module = models.CharField(max_length=32, choices=Module.choices)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    warehouse = models.ForeignKey(
        'warehouse.WareHouse', on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs',
    )
    changes = models.JSONField(default=dict, blank=True)
    request_key = models.UUIDField(null=True, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)

    objects = AppendOnlyManager()

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [
            models.Index(fields=['module', 'action', 'created_at'], name='common_audit_mod_act_idx'),
            models.Index(fields=['model_name', 'object_id'], name='common_audit_model_obj_idx'),
            models.Index(fields=['warehouse', 'created_at'], name='common_audit_wh_created_idx'),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('AuditLog is append-only and cannot be updated.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('AuditLog is append-only and cannot be deleted.')

    def __str__(self):
        return f'{self.module}:{self.action}:{self.model_name}#{self.object_id}'


class ImportJob(TimeStampedModel):
    class Status(models.TextChoices):
        UPLOADED = 'uploaded', 'Uploaded'
        VALIDATED = 'validated', 'Validated'
        COMMITTED = 'committed', 'Committed'
        FAILED = 'failed', 'Failed'

    kind = models.CharField(max_length=64, db_index=True)
    file = models.FileField(upload_to='imports/', blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UPLOADED)
    dry_run_result = models.JSONField(default=dict, blank=True)
    committed_result = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='import_jobs',
    )
    request_key = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return f'{self.kind} ({self.status}) #{self.pk}'


class _SingletonModel(TimeStampedModel):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1
        kwargs.pop('force_insert', None)
        super().save(*args, **kwargs)


class POSSetting(_SingletonModel):
    class ReceiptFormat(models.TextChoices):
        A4 = 'a4', 'A4'
        THERMAL58 = 'thermal58', 'Thermal 58mm'
        THERMAL80 = 'thermal80', 'Thermal 80mm'

    default_customer = models.ForeignKey('people.Party', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    default_biller = models.ForeignKey('people.Biller', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    default_warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    keyboard_shortcuts = models.BooleanField(default=True)
    product_grid_count = models.PositiveSmallIntegerField(default=12)
    receipt_auto_print = models.BooleanField(default=False)
    receipt_format = models.CharField(max_length=16, choices=ReceiptFormat.choices, default=ReceiptFormat.THERMAL80)
    payment_options = models.JSONField(default=list, blank=True)
    sms_after_sale = models.BooleanField(default=False)


class HRMSetting(_SingletonModel):
    default_check_in = models.TimeField(default='09:00:00')
    default_check_out = models.TimeField(default='17:00:00')
    attendance_import_config = models.JSONField(default=dict, blank=True)
    payroll_month_policy_freeze = models.BooleanField(default=True)
    carry_expiry_month_day = models.CharField(max_length=5, default='03-31')


class RewardSetting(_SingletonModel):
    enable_reward_points = models.BooleanField(default=False)
    sold_amount_per_point = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal('100.0000'))
    minimum_sold_amount_to_get_point = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal('0.0000'))
    redeem_amount_per_point = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal('1.0000'))
    point_expiry_duration_days = models.PositiveIntegerField(null=True, blank=True)


class TaskSetting(_SingletonModel):
    block_close_with_open_subtasks = models.BooleanField(default=True)


class Notification(TimeStampedModel):
    title = models.CharField(max_length=200)
    body = models.TextField()
    audience = models.CharField(max_length=64, blank=True, default='all')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')


class NotificationRecipient(models.Model):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name='recipients')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['notification', 'user'], name='common_notif_recipient_unique'),
        ]


class SMSTemplate(TimeStampedModel):
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=150)
    body = models.TextField()
    is_active = models.BooleanField(default=True)


class MailSetting(_SingletonModel):
    host = models.CharField(max_length=255, blank=True)
    port = models.PositiveIntegerField(default=587)
    username = models.CharField(max_length=255, blank=True)
    password_encrypted = models.TextField(blank=True)
    from_email = models.EmailField(blank=True)
    use_tls = models.BooleanField(default=True)
    is_active = models.BooleanField(default=False)


class SMSSetting(_SingletonModel):
    provider = models.CharField(max_length=64, blank=True, default='stub')
    sender_id = models.CharField(max_length=64, blank=True)
    api_key_encrypted = models.TextField(blank=True)
    is_active = models.BooleanField(default=False)


class OutboxMessage(TimeStampedModel):
    class Channel(models.TextChoices):
        SMS = 'sms', 'SMS'
        MAIL = 'mail', 'Mail'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'

    channel = models.CharField(max_length=12, choices=Channel.choices)
    template = models.CharField(max_length=64, blank=True)
    recipient = models.CharField(max_length=255)
    payload = models.JSONField(default=dict)
    source_event_key = models.CharField(max_length=128, unique=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ['created_at', 'pk']


class CustomField(TimeStampedModel):
    class Entity(models.TextChoices):
        PRODUCT = 'product', 'Product'
        CUSTOMER = 'customer', 'Customer'
        SALE = 'sale', 'Sale'
        PURCHASE = 'purchase', 'Purchase'
        BILLER = 'biller', 'Biller'

    class FieldType(models.TextChoices):
        TEXT = 'text', 'Text'
        NUMBER = 'number', 'Number'
        TEXTAREA = 'textarea', 'Textarea'
        SELECT = 'select', 'Select'
        MULTI_SELECT = 'multi_select', 'Multi-select'
        CHECKBOX = 'checkbox', 'Checkbox'
        RADIO = 'radio', 'Radio'
        DATE = 'date', 'Date'

    entity = models.CharField(max_length=24, choices=Entity.choices, db_index=True)
    label = models.CharField(max_length=120)
    key = models.SlugField(max_length=64)
    field_type = models.CharField(max_length=24, choices=FieldType.choices, default=FieldType.TEXT)
    default = models.JSONField(null=True, blank=True)
    options = models.JSONField(default=list, blank=True)
    required = models.BooleanField(default=False)
    show_in_table = models.BooleanField(default=False)
    show_in_invoice = models.BooleanField(default=False)
    admin_only = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['entity', 'key'], name='common_custom_field_entity_key_unique'),
        ]


class CustomFieldValue(TimeStampedModel):
    field = models.ForeignKey(CustomField, on_delete=models.CASCADE, related_name='values')
    object_id = models.BigIntegerField(db_index=True)
    value = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['field', 'object_id'], name='common_custom_field_val_unique'),
        ]


class RestaurantTable(TimeStampedModel):
    name = models.CharField(max_length=80)
    code = models.CharField(max_length=32, unique=True)
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, null=True, blank=True, related_name='tables')
    capacity = models.PositiveSmallIntegerField(default=4)
    is_active = models.BooleanField(default=True)


class BarcodeSetting(TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    paper = models.CharField(max_length=32, default='A4')
    width_mm = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('50'))
    height_mm = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('30'))
    margins = models.JSONField(default=dict, blank=True)
    rows = models.PositiveSmallIntegerField(default=5)
    columns = models.PositiveSmallIntegerField(default=3)
    thermal_width = models.PositiveSmallIntegerField(default=58)
    is_default = models.BooleanField(default=False)


class Language(TimeStampedModel):
    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=80)
    is_rtl = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)


class PaymentGatewayConfig(TimeStampedModel):
    class Provider(models.TextChoices):
        PAYPAL = 'paypal', 'PayPal'
        STRIPE = 'stripe', 'Stripe'
        RAZORPAY = 'razorpay', 'Razorpay'
        PAYSTACK = 'paystack', 'Paystack'
        MOLLIE = 'mollie', 'Mollie'
        XENDIT = 'xendit', 'Xendit'
        BKASH = 'bkash', 'bKash'
        SSLCOMMERZ = 'sslcommerz', 'SSLCommerz'
        MPESA = 'mpesa', 'M-Pesa'
        PESAPAL = 'pesapal', 'Pesapal'

    class Mode(models.TextChoices):
        SANDBOX = 'sandbox', 'Sandbox'
        LIVE = 'live', 'Live'

    provider = models.CharField(max_length=32, choices=Provider.choices, unique=True)
    enabled = models.BooleanField(default=False)
    mode = models.CharField(max_length=12, choices=Mode.choices, default=Mode.SANDBOX)
    credentials_encrypted = models.TextField(blank=True)

    class Meta:
        permissions = [('manage_gateways', 'Can manage payment gateway configurations')]


class PaymentAttempt(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SUCCEEDED = 'succeeded', 'Succeeded'
        FAILED = 'failed', 'Failed'

    provider = models.CharField(max_length=32, choices=PaymentGatewayConfig.Provider.choices)
    order = models.ForeignKey('sale.SalesOrder', on_delete=models.PROTECT, related_name='gateway_attempts')
    account = models.ForeignKey('accounting.FinancialAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    amount = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(Decimal('0.0001'))])
    currency = models.CharField(max_length=3, default='BDT')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    provider_ref = models.CharField(max_length=150, blank=True)
    callback_verified = models.BooleanField(default=False)
    request_key = models.UUIDField(default=uuid4, unique=True, editable=False)


class BackupSetting(_SingletonModel):
    enabled = models.BooleanField(default=False)
    backup_dir = models.CharField(max_length=255, blank=True)
    retention_days = models.PositiveSmallIntegerField(default=7)

    class Meta:
        permissions = [('run_backup', 'Can run database backups')]
