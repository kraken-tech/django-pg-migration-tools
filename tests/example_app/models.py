from django.db import models


class IntModel(models.Model):
    int_field = models.IntegerField(default=0)


class CharModel(models.Model):
    char_field = models.CharField(default="char")

    class Meta:
        indexes = (models.Index(fields=["char_field"], name="char_field_idx"),)
