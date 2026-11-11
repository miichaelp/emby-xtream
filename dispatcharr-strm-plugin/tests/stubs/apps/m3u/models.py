from django.db import models


class M3UAccount(models.Model):
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    account_type = models.CharField(max_length=10, default="XC")
    priority = models.PositiveIntegerField(default=0)
    server_url = models.CharField(max_length=255, default="http://provider")
    username = models.CharField(max_length=255, default="user")
    password = models.CharField(max_length=255, default="pass")

    def get_user_agent_string(self):
        return "test"

    class Meta:
        app_label = "m3u"
