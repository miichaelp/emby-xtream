from django.db import models


class M3UAccount(models.Model):
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    account_type = models.CharField(max_length=10, default="XC")
    priority = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = "m3u"
