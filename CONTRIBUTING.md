# Contributing

## Installing

Ensure that you have one of the supported Python versions (see README)
installed locally:

```sh
python --version
```

Ensure that you have the `uv` package installed. For installation details refer
to [**uv**](https://github.com/astral-sh/uv).

Create a virtual environment using your favourite method. If you don't already
have a way of managing a virtual environment, you can run:

```sh
# Create the virtual environment at /path/to/new/virtual-env:
python -m venv /path/to/new/virtual-env

# Activate the new virtual environment:
source /path/to/new/virtual-env/
```

Install the development dependencies by running:

```sh
make dev
```

Install pre-commit as a **system** dependency. Refer to
[**pre-commit**](https://pre-commit.com/) for installation details. Then run:

```sh
pre-commit install
```

## Testing (single Python version)

To run the test suite using the Python version of your virtual environment,
run:

```sh
make test
```

## Testing (all supported Python versions)

To test against all supported Python (and relevant package) versions, have
`nox` installed as a **system** dependency. Refer to
[**nox**](https://nox.thea.codes/en/stable/) for installation details.

Ensure that all the supported Python versions (see README) are installed on
your system. For example, `python3.10`, `python3.11`, etc. This can be done
with [**pyenv**](https://github.com/pyenv/pyenv), or your operating system
might have its own way of providing these packages for you.

Then run `nox`:

```sh
nox
```

## Static analysis

Run all static analysis tools with:

```sh
make lint
```

## Auto formatting

Reformat code to conform with our conventions using:

```sh
make format
```

## Dependencies

Package dependencies are declared in `pyproject.toml`.

- _package_ dependencies in the `dependencies` array in the `[project]`
  section.
- _development_ dependencies in the `dev` array in the
  `[project.optional-dependencies]` section.

For local development, the dependencies declared in `pyproject.toml` are pinned
to specific versions using the `requirements/development.txt` lock file.

### Adding a new dependency

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

### Removing a dependency

Removing Python dependencies works exactly the same way: edit `pyproject.toml`
and then run `make dev`.

### Updating all Python packages

To update the pinned versions of all packages simply run:

```sh
make update
```

This will update the pinned versions of every development, test, and docs lock
files to the latest version which is compatible with the constraints in
`pyproject.toml`.

You can then run:

```sh
make dev
```

This will sync your installed packages with the updated versions pinned in
`requirements/development.txt`.

### Updating individual Python packages

Upgrade a single dependency with:

```sh
pip-compile -P $PACKAGE==$VERSION pyproject.toml --resolver=backtracking --extra=dev --output-file=requirements/development.txt
```

You can then run:

```sh
make dev
```

This will sync your installed packages with the updated versions pinned in
`requirements/development.txt`.

## Publishing

This package is built and uploaded to PyPI automatically by the
`build_and_publish` workflow in GitHub Actions. This workflow will be run
whenever a tag is pushed for a new version; the tag must start with v.

To publish a new version of this package:

1. Update the version in pyproject.toml.
2. Check all changes have been recorded in the changelog.
3. Add a heading in the changelog for the new version, including today's date.
4. Check if the README.md "Main Features" section needs updating.
5. Commit the changes and open a PR.
6. Once the PR is approved, rebase the branch to get the latest changes and tag
   the merge commit with the new version:
   ```sh
   git tag 'v0.0.0'
   git push origin --tags
   ```
7. The build_and_publish workflow will verify that the tag, package version,
   and changelog all contain the same version number and publish the package
   to PyPI.

### The publishing workflow failed, what can I do?

If the build_and_publish workflow failed, the package will not be published to
PyPI. After doing whatever is needed to fix the workflow you can:

1. Rebase and push your branch.
2. Force the regeneration for the tag:
   ```sh
   git tag v0.0.0 -f
   ```
3. Then update the tag on the remote:
   ```sh
   git push origin v0.0.0 -f
   ```
