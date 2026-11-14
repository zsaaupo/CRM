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
        ordering = ['name', 'pk']
        verbose_name_plural = 'statuses'

    def __str__(self):
        return self.name
