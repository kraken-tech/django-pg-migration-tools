import django
from django.db import models
from django.db.models import functions


def get_check_constraint(condition: models.Q, name: str) -> models.CheckConstraint:
    if django.VERSION >= (5, 1):
        # https://docs.djangoproject.com/en/5.1/releases/5.1/
        # The check keyword argument of CheckConstraint is deprecated in
        # favor of condition.
        return models.CheckConstraint(
            condition=condition,
            name=name,
        )
    else:
        return models.CheckConstraint(
            check=condition,
            name=name,
        )


class IntModel(models.Model):
    int_field = models.IntegerField(default=0)


class IntModelWithExplicitPK(models.Model):
    explicit_pk = models.IntegerField(primary_key=True, db_column="id32")


class ModelWithForeignKey(models.Model):
    fk = models.ForeignKey(IntModel, null=True, on_delete=models.CASCADE)


class ModelWithNotNullForeignKey(models.Model):
    fk = models.ForeignKey(IntModel, null=False, on_delete=models.CASCADE)


class CharModel(models.Model):
    char_field = models.CharField(default="char")

    class Meta:
        indexes = (models.Index(fields=["char_field"], name="char_field_idx"),)
        constraints = (
            models.UniqueConstraint(fields=["char_field"], name="unique_char_field"),
        )


class UniqueConditionCharModel(models.Model):
    char_field = models.CharField(default="char")

    class Meta:
        indexes = (
            models.Index(fields=["char_field"], name="unique_condition_char_field_idx"),
        )
        constraints = (
            models.UniqueConstraint(
                fields=["char_field"],
                name="unique_char_field_with_condition",
                condition=models.Q(char_field__in=["c", "something"]),
            ),
        )


class UniqueExpressionCharModel(models.Model):
    char_field = models.CharField(default="char")

    class Meta:
        indexes = (
            models.Index(
                fields=["char_field"], name="unique_expression_char_field_idx"
            ),
        )
        constraints = (
            models.UniqueConstraint(
                functions.Lower("char_field"),
                name="unique_char_field_with_expression",
            ),
        )


class NullIntFieldModel(models.Model):
    int_field = models.IntegerField(null=True)


class NullFKFieldModel(models.Model):
    fk = models.ForeignKey(IntModel, null=True, on_delete=models.CASCADE)


class NotNullIntFieldModel(models.Model):
    # null=False is the default, but we set it here for clarity.
    int_field = models.IntegerField(null=False)


class CharIDModel(models.Model):
    id = models.CharField(max_length=42, primary_key=True)


class ModelWithCheckConstraint(models.Model):
    class Meta:
        constraints = (
            get_check_constraint(
                condition=models.Q(id=42),
                name="id_must_be_42",
            ),
        )
