from django.db import models


class IntModel(models.Model):
    int_field = models.IntegerField(default=0)


class CharModel(models.Model):
    char_field = models.CharField(default="char")

    class Meta:
        indexes = (models.Index(fields=["char_field"], name="char_field_idx"),)
        constraints = (
            models.UniqueConstraint(fields=["char_field"], name="unique_char_field"),
        )


class AnotherCharModel(models.Model):
    char_field = models.CharField(default="char")

    class Meta:
        indexes = (models.Index(fields=["char_field"], name="another_char_field_idx"),)
        constraints = (
            models.UniqueConstraint(
                fields=["char_field"],
                name="unique_char_field_with_condition",
                condition=models.Q(char_field__in=["c", "something"]),
            ),
        )


class NullIntFieldModel(models.Model):
    int_field = models.IntegerField(null=True)


class NotNullIntFieldModel(models.Model):
    # null=False is the default, but we set it here for clarity.
    int_field = models.IntegerField(null=False)


class CharIDModel(models.Model):
    id = models.CharField(max_length=42, primary_key=True)
