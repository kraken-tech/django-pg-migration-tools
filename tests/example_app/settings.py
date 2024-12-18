from environs import Env


env = Env()


DATABASES = {
    "default": env.dj_db_url(
        "DATABASE_URL",
        # Defaults to "user: postgres" and "pass: postgres" for convenience
        # in local development.
        default="postgres://postgres:postgres@localhost/django_pg_migration_tools",
    ),
}
# "secondary" is just an alias to serve multiple connections for tests
# that need it.
DATABASES["secondary"] = DATABASES["default"]

SECRET_KEY = "test-secret-key"
INSTALLED_APPS = [
    "tests.example_app",
    "django_pg_migration_tools",
]
USE_TZ = True
