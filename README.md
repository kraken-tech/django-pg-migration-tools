# Django Postgres Migration Tools

Django Postgres Migration Tools provides extra functionalities to make Django
migrations safer and more scalable.

## Prerequisites

This package supports:

- Python 3.10
- Python 3.11
- Python 3.12

During development you will also need:

- pre-commit 3 _(Optional, but strongly recommended)_

## Local development

When making changes please remember to update the `CHANGELOG.md`, which follows
the guidelines at [keepachangelog]. Add your changes to the `[Unreleased]`
section when you create your PR.

[keepachangelog]: https://keepachangelog.com/

### Installation

Ensure one of the above Pythons is installed and used by the `python`
executable:

```sh
python --version
Python 3.11.9   # or any of the supported versions
```

Ensure `uv` is installed as a system package. This can be done with `pipx` or
Homebrew.

Then create and activate a virtual environment. If you don't have any other way
of managing virtual environments this can be done by running:

```sh
uv venv
source .venv/bin/activate
```

You could also use [virtualenvwrapper], [direnv] or any similar tool to help
manage your virtual environments.

Once you are in an active virtual environment run:

```sh
make dev
```

This will set up your local development environment, installing all development
dependencies.

[virtualenvwrapper]: https://virtualenvwrapper.readthedocs.io/
[direnv]: https://direnv.net

### Testing (single Python version)

To run the test suite using the Python version of your virtual environment,
run:

```sh
make test
```

### Testing (all supported Python versions)

To test against multiple Python (and package) versions, we need to:

- Have [`nox`][nox] installed outside of the virtualenv. This is best done
  using `pipx`:

  ```sh
  pipx install nox
  ```

- Ensure that all supported Python versions are installed and available on your
  system (as e.g. `python3.10`, `python3.11` etc). This can be done with
  `pyenv`.

Then run `nox` with:

```sh
nox
```

Nox will create a separate virtual environment for each combination of Python
and package versions defined in `noxfile.py`.

To list the available sessions, run:

```sh
nox --list-sessions
```

To run the test suite in a specific Nox session, use:

```sh
nox -s $SESSION_NAME
```

[nox]: https://nox.thea.codes/en/stable/

### Static analysis

Run all static analysis tools with:

```sh
make lint
```

### Auto formatting

Reformat code to conform with our conventions using:

```sh
make format
```

### Dependencies

Package dependencies are declared in `pyproject.toml`.

- _package_ dependencies in the `dependencies` array in the `[project]`
  section.
- _development_ dependencies in the `dev` array in the
  `[project.optional-dependencies]` section.

For local development, the dependencies declared in `pyproject.toml` are pinned
to specific versions using the `requirements/development.txt` lock file.

#### Adding a new dependency

To install a new Python dependency add it to the appropriate section in
`pyproject.toml` and then run:

```sh
make dev
```

This will:

1. Build a new version of the `requirements/development.txt` lock file
   containing the newly added package.
2. Sync your installed packages with those pinned in
   `requirements/development.txt`.

This will not change the pinned versions of any packages already in any
requirements file unless needed by the new packages, even if there are updated
versions of those packages available.

Remember to commit your changed `requirements/development.txt` files alongside
the changed `pyproject.toml`.

#### Removing a dependency

Removing Python dependencies works exactly the same way: edit `pyproject.toml`
and then run `make dev`.

#### Updating all Python packages

To update the pinned versions of all packages simply run:

```sh
make update
```

This will update the pinned versions of every package in the
`requirements/development.txt` and `requirements/pytest-in-nox` (required for
CI) lock files to the latest version which is compatible with the constraints
in `pyproject.toml`.

You can then run:

```sh
make dev
```

to sync your installed packages with the updated versions pinned in
`requirements/development.txt`.

#### Updating individual Python packages

Upgrade a single production dependency with:

```sh
pip-compile -P $PACKAGE==$VERSION pyproject.toml --resolver=backtracking --extra=dev --output-file=requirements/development.txt
```

You can then run:

```sh
make dev
```

to sync your installed packages with the updated versions pinned in
`requirements/development.txt`.
