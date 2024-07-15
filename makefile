PIP_VERSION=24.0
SHELL=/bin/bash

# If we're running in CI then store Pytest output in a format which CircleCI can parse
ifdef CIRCLECI
MYPY_ARGS=--junit-xml=test-results/mypy.xml
endif

# Standard entry points
# =====================

.PHONY:dev
dev: install_python_packages .git/hooks/pre-commit

.PHONY:test
test:
	pytest

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

.PHONY:package
package:
	python -m build

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