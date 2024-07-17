from django.db import models


class IntModel(models.Model):
    int_field = models.IntegerField(default=0)
