from django.db import models
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


class ERPBranding(TimeStampedModel):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    logo = models.BinaryField(default=bytes, blank=True)
    logo_revision = models.UUIDField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = 'ERP branding'
        verbose_name_plural = 'ERP branding'
        default_permissions = ('view', 'change')
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='common_erpbranding_singleton')]

    def __str__(self):
        return 'ERP logo'
