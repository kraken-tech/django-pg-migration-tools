PIP_VERSION=24.0
SHELL=/bin/bash
COVERAGE_FILE=build/coverage
COVERAGE_HTML_FOLDER=build/coverage_html

.PHONY:help
help:
	@echo "Available targets:"
	@echo "  clean: Remove all build artifacts at the build/ directory."
	@echo "  coverage: Run code coverage and print results."
	@echo "  coverage_html: Builds an HTML coverage report and opens it with \$$BROWSER."
	@echo "  coverage_report: Only report on already computed coverage results."
	@echo "  coverage_run: Only run code coverage and store results."
	@echo "  dev: Install all dev dependencies and this package in editable mode."
	@echo "  docs: Build the docs at the build/ directory"
	@echo "  help: Show this help message."
	@echo "  lint: Run formatters and static analysis checks."
	@echo "  matrix_test: Run matrix testing locally."
	@echo "  test: Run tests locally."
	@echo "  update: Update package dependencies."
	@echo "  version_major: Bump the project's version number to next major version."
	@echo "  version_minor: Bump the project's version number to next minor version."
	@echo "  version_patch: Bump the project's version number to next patch version."

# Standard entry points
# =====================

.PHONY:dev
dev: install_python_packages .git/hooks/pre-commit
	uv pip install -e .

.PHONY:test
test:
	python -m pytest $(PYTEST_FLAGS)

.PHONY:coverage_run
coverage_run:
	coverage run --branch \
	--include=src/django_pg_migration_tools/*,tests/django_pg_migration_tools/* \
	--data-file=$(COVERAGE_FILE) \
	-m pytest

.PHONY:coverage_report
coverage_report:
	coverage report --skip-empty --format=$(COVERAGE_FORMAT) --data-file=$(COVERAGE_FILE)

.PHONY:coverage
coverage: coverage_run coverage_report

.PHONY:coverage_html
coverage_html: coverage
	coverage html --data-file=$(COVERAGE_FILE) -d $(COVERAGE_HTML_FOLDER)
	$$BROWSER $(COVERAGE_HTML_FOLDER)/index.html

.PHONY:matrix_test
matrix_test:
	nox

.PHONY:lint
lint: ruff_format ruff_lint mypy

.PHONY:ruff_format
ruff_format:
	ruff format --check .

.PHONY:ruff_lint
ruff_lint:
	ruff check .

.PHONY:mypy
mypy:
	mypy $(MYPY_ARGS)

.PHONY:format
format:
	ruff format .
	ruff check --fix .

.PHONY:update
update:
	uv pip compile pyproject.toml -q --upgrade --resolver=backtracking --extra=dev --output-file=requirements/development.txt
	uv pip compile pyproject.toml -q --upgrade --resolver=backtracking --extra=pytest-in-nox-psycopg3 --output-file=requirements/pytest-in-nox-psycopg3.txt
	uv pip compile pyproject.toml -q --upgrade --resolver=backtracking --extra=pytest-in-nox-psycopg2 --output-file=requirements/pytest-in-nox-psycopg2.txt
	uv pip compile pyproject.toml -q --upgrade --resolver=backtracking --extra=docs --output-file=requirements/docs.txt

.PHONY:docs
docs:
	sphinx-build docs/ build/docs -T -b html --fail-on-warning

.PHONY:clean
clean:
	rm -rf build

.PHONY:package
package:
	python -m build

.PHONY:version_major
version_major:
	bump-my-version bump major
	@echo Version number updated to `bump-my-version show current_version`

.PHONY:version_minor
version_minor:
	bump-my-version bump minor
	@echo Version number updated to `bump-my-version show current_version`

.PHONY:version_patch
version_patch:
	bump-my-version bump patch
	@echo Version number updated to `bump-my-version show current_version`

# Implementation details
# ======================

# Pip install all required Python packages
.PHONY:install_python_packages
install_python_packages: install_prerequisites requirements/development.txt
	uv pip sync requirements/development.txt requirements/firstparty.txt

# This target _could_ run both `pip install` commands unconditionally because `pip install` is idempotent if versions
# have not changed. The benefits of checking the version number before installing are that if there's nothing to do then
# (a) it's faster and (b) it produces less noisy output.
.PHONY:install_prerequisites
install_prerequisites:
	@if [ `uv pip show pip 2>/dev/null | awk '/^Version:/ {print $$2}'` != "$(PIP_VERSION)" ]; then \
		uv pip install pip==$(PIP_VERSION); \
	fi

# Add new dependencies to requirements/development.txt whenever pyproject.toml changes
requirements/development.txt: pyproject.toml
	uv pip compile pyproject.toml -q --resolver=backtracking --extra=dev --output-file=requirements/development.txt

.git/hooks/pre-commit:
	@if type pre-commit >/dev/null 2>&1; then \
		pre-commit install; \
	else \
		echo "WARNING: pre-commit not installed." > /dev/stderr; \
	fi
