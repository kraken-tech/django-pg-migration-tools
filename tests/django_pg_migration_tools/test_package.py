import django_pg_migration_tools as package


def test_has_docstring():
    assert package.__doc__ is not None
