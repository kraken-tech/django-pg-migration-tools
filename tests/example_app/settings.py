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
SECRET_KEY = "test-secret-key"
INSTALLED_APPS = [
    "tests.example_app",
]
USE_TZ = True
