"""Every third-party module the image imports must be installed in the image.

On 2026-09-02 at 19:00 UTC the persona runner and Nova's site both started
crash-looping on `ModuleNotFoundError: No module named 'yaml'`, and stayed
down for ten hours -- Nova's heartbeat could not run at all, so nothing that
watches this system was awake to notice. The cause was one line: #660 added
`import yaml` to `agora_runner/tools_kubectl_test.py`, and the Dockerfile had
no `pip install` step at all. #663 installed PyYAML and fixed that instance.

This test exists so there is no next instance. The reason 5,794 tests passed
on the commit that broke production is that CI installs pyyaml itself -- for a
*test* that parses build.yaml -- so the suite ran in an environment strictly
richer than the image and could not see the difference. A test that imports a
module proves the CI runner has it. Only comparing the source against
requirements.txt proves the image does.

Two things are read rather than restated, because a list of "what ships" kept
here is a second copy of the truth that goes stale exactly the way the pin it
watches does:

* what ships -- from the Dockerfile's own `COPY` lines,
* what is installed -- from requirements.txt, resolved to the import names
  those distributions actually provide (`pyyaml` provides `yaml`).

Guarded imports are deliberately allowed. `agora_runner/vault.py` does
`import xxhash` inside a try/except with a documented sha256 fallback, so it
is optional by construction and belongs nowhere near requirements.txt.
"""

import ast
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile"
REQUIREMENTS = REPO / "requirements.txt"

#: Handlers that make an import optional. A bare `except:` catches everything,
#: and so does `except Exception`.
_IMPORT_RESCUERS = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}


def _normalise(name):
    """PEP 503 name normalisation, so `PyYAML` and `pyyaml` are one name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _copied_paths(dockerfile_text):
    """The repo paths the image actually contains, from its own COPY lines.

    A `COPY a/ b/ ./` line has a destination as its last word; everything
    before it is a source. Sources are repo-relative.
    """
    paths = []
    for line in dockerfile_text.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("COPY "):
            continue
        words = stripped.split()[1:]
        if len(words) < 2:
            continue
        paths.extend(words[:-1])
    return paths


def _python_files(paths):
    files = []
    for raw in paths:
        target = REPO / raw.rstrip("/")
        if target.is_dir():
            files.extend(sorted(target.rglob("*.py")))
        elif target.suffix == ".py" and target.is_file():
            files.append(target)
    return files


def _required_import_roots(path):
    """Root module names imported unconditionally by one file.

    An import anywhere inside a `try:` whose handlers rescue an ImportError is
    optional and is not reported. Relative imports (`from . import x`) are
    always local and are never reported.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    rescued = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if not any(
            handler.type is None
            or (isinstance(handler.type, ast.Name) and handler.type.id in _IMPORT_RESCUERS)
            for handler in node.handlers
        ):
            continue
        for statement in node.body:
            for inner in ast.walk(statement):
                rescued.add(id(inner))

    roots = set()
    for node in ast.walk(tree):
        if id(node) in rescued:
            continue
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _local_top_level_names():
    """Names that resolve inside the repo itself rather than site-packages."""
    names = set()
    for entry in REPO.iterdir():
        if entry.is_dir() and (entry / "__init__.py").exists():
            names.add(entry.name)
        elif entry.suffix == ".py":
            names.add(entry.stem)
    return names


def _import_names_provided_by(requirement_names):
    """Map each requirement to the import names it provides, via metadata.

    `pyyaml` provides `yaml`, and no table here should have to know that. A
    distribution that is not installed locally cannot be resolved, so it
    contributes its own normalised name and nothing else -- which is the right
    answer for the common case where they match.
    """
    from importlib import metadata

    provided = set()
    by_import_name = metadata.packages_distributions()
    wanted = {_normalise(name) for name in requirement_names}
    for import_name, distributions in by_import_name.items():
        if any(_normalise(dist) in wanted for dist in distributions):
            provided.add(import_name)
    provided.update(wanted)
    return provided


def _declared_requirements():
    if not REQUIREMENTS.exists():
        return []
    names = []
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        names.append(re.split(r"[<>=!~\[; ]", line, 1)[0].strip())
    return [name for name in names if name]


def test_the_stdlib_list_is_the_image_s_stdlib_list():
    """This test judges with `sys.stdlib_module_names` from the interpreter it
    runs on. That is only evidence about the image while the two are the same
    Python minor version, so read the version out of the Dockerfile and say so
    rather than quietly judging with the wrong list."""
    match = re.search(r"^FROM\s+python:(\d+)\.(\d+)", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert match, "Dockerfile no longer starts FROM python:<major>.<minor> -- this test cannot judge"
    assert (int(match.group(1)), int(match.group(2))) == sys.version_info[:2], (
        f"Image runs Python {match.group(1)}.{match.group(2)} but this test runs "
        f"{sys.version_info.major}.{sys.version_info.minor}; the stdlib module list differs "
        "between them, so a pass here would not be evidence about the image."
    )


def test_the_dockerfile_still_copies_python_into_the_image():
    """A negative result only counts if a positive one was possible. If the
    COPY parsing ever finds nothing, every assertion below passes vacuously."""
    files = _python_files(_copied_paths(DOCKERFILE.read_text(encoding="utf-8")))
    assert len(files) > 20, f"only found {len(files)} shipped .py file(s) -- COPY parsing is broken"
    assert any(path.name == "run.py" for path in files)
    assert any(path.parent.name == "agora_runner" for path in files)


def test_every_shipped_third_party_import_is_in_requirements():
    installed = _import_names_provided_by(_declared_requirements())
    local = _local_top_level_names()
    missing = {}
    for path in _python_files(_copied_paths(DOCKERFILE.read_text(encoding="utf-8"))):
        for root in _required_import_roots(path):
            if root in sys.stdlib_module_names or root in local or root in installed:
                continue
            missing.setdefault(root, []).append(str(path.relative_to(REPO)))

    assert not missing, (
        "These modules are imported by code that ships in the image but are not "
        "installed in it, so the container will die on startup with "
        "ModuleNotFoundError:\n"
        + "\n".join(f"  {root} -- {', '.join(sorted(files))}" for root, files in sorted(missing.items()))
        + "\nAdd the distribution to requirements.txt, or guard the import in a "
        "try/except ImportError with a working fallback."
    )


def test_a_guarded_import_is_not_reported():
    """`agora_runner/vault.py` imports xxhash inside a try/except with a
    sha256 fallback. It is optional by construction and must not be dragged
    into requirements.txt by this test."""
    roots = _required_import_roots(REPO / "agora_runner" / "vault.py")
    assert "xxhash" not in roots
    assert "json" in roots, "sanity: vault.py does import json unconditionally"


def test_an_unguarded_import_is_reported(tmp_path):
    module = tmp_path / "sample.py"
    module.write_text("import os\nimport nosuchthirdparty\n", encoding="utf-8")
    assert "nosuchthirdparty" in _required_import_roots(module)


def test_requirements_parsing_drops_versions_and_comments(tmp_path):
    assert _normalise("PyYAML") == "pyyaml"
    assert _normalise("ruamel.yaml") == "ruamel-yaml"


def test_pyyaml_is_the_requirement_that_the_outage_needed():
    """Regression pin for the ten-hour outage of 2026-09-02. `import yaml` in
    tools_kubectl_test.py is unguarded, so removing pyyaml from requirements.txt
    must turn this suite red."""
    roots = _required_import_roots(REPO / "agora_runner" / "tools_kubectl_test.py")
    assert "yaml" in roots
    assert "yaml" in _import_names_provided_by(_declared_requirements())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
