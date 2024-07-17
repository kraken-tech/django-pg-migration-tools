import pytest

from tests.example_app import models as test_models


@pytest.mark.django_db
def test_has_database() -> None:
    # To be replaced by real tests once repo skeleton is completed.
    assert test_models.IntModel.objects.count() == 0
