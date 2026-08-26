"""Nothing watched a Helm repoURL until Cycle 484 -- and a 200 is not the check.

The outage this was built for: `bitnami-labs/sealed-secrets` was
transferred to `bitnami/sealed-secrets`, GitHub Pages stopped serving the
old chart index, and ArgoCD could not diff the component that decrypts
every credential in this cluster for three hours before a cycle tripped
over it.

The half that could go wrong quietly is the *positive* result. A GitHub
Pages 404 is a 200-shaped HTML page from a working server -- the dead URL
above returned 9,115 bytes of it -- so a check that read the status code
would have gone green on the exact failure it exists to catch. Hence
`serves_html_with_a_200_is_not_an_index`, which is the test that pins the
design rather than the plumbing.
"""

import io
import json
import subprocess
import urllib.error

from tools import helm_repo_health


def kubectl(items):
    """A fake `subprocess.run` that answers `kubectl get applications`."""
    def runner(cmd, **kwargs):
        assert cmd[0] == "kubectl", cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"items": items}), stderr="")
    return runner


def app(name, repo=None, chart=None, revision="1.*", sources=None, namespace="argocd"):
    spec = {}
    if sources is not None:
        spec["sources"] = sources
    if repo is not None:
        spec["source"] = {"repoURL": repo, "chart": chart,
                          "targetRevision": revision}
        if chart is None:
            del spec["source"]["chart"]
    return {"metadata": {"name": name, "namespace": namespace}, "spec": spec}


def serving(**repos):
    """A fake urlopen. Keys are hosts-ish tokens, values are raw bodies."""
    def opener(request, timeout=None):
        url = request.full_url
        for token, body in repos.items():
            if token in url:
                if isinstance(body, int):
                    raise urllib.error.HTTPError(url, body, "Not Found", {}, None)
                return _Response(body)
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    return opener


class _Response:
    def __init__(self, body):
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


INDEX = "apiVersion: v1\nentries:\n  sealed-secrets:\n  - version: 2.19.3\n"


def test_a_dead_repo_url_is_exit_2_and_names_the_application():
    status, lines = check_with(
        [app("sealed-secrets", "https://bitnami-labs.github.io/sealed-secrets",
             "sealed-secrets")],
        serving(**{"bitnami-labs": 404}))
    assert status == 2
    assert any("BROKEN" in line and "argocd/sealed-secrets" in line
               and "404" in line for line in lines), lines


def test_serves_html_with_a_200_is_not_an_index():
    """The failure a status-code check would have called healthy.

    GitHub Pages answers a missing project site with a styled HTML page,
    and some hosts answer any path at all. Both are 200s carrying no
    chart, which is the whole reason this parses the body.
    """
    status, lines = check_with(
        [app("sealed-secrets", "https://example.test/charts", "sealed-secrets")],
        serving(**{"example.test": "<!DOCTYPE html><title>404</title>"}))
    assert status == 2
    assert any("not a Helm index" in line for line in lines), lines


def test_an_index_without_the_named_chart_is_broken():
    status, lines = check_with(
        [app("sealed-secrets", "https://example.test/charts", "sealed-secrets")],
        serving(**{"example.test": "apiVersion: v1\nentries:\n  reloader:\n  - version: 1.0\n"}))
    assert status == 2
    assert any("no chart named sealed-secrets" in line for line in lines), lines


def test_every_repo_serving_its_chart_is_exit_0_and_says_what_it_swept():
    status, lines = check_with(
        [app("sealed-secrets", "https://example.test/charts", "sealed-secrets")],
        serving(**{"example.test": INDEX}))
    assert status == 0
    assert any("ok" in line and "argocd/sealed-secrets" in line for line in lines), lines
    assert any("Read 1 Helm source(s)" in line for line in lines), lines


def test_kubectl_refused_is_exit_1_and_never_reads_as_clean():
    def refused(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr='Error from server (Forbidden)')
    status, lines = helm_repo_health.check(runner=refused, opener=serving())
    assert status == 1
    assert any("COULD NOT READ" in line for line in lines), lines
    assert not any(line.startswith("ok") for line in lines), lines


def test_a_git_only_application_is_not_a_helm_source():
    status, lines = check_with(
        [app("platform-config", "https://github.com/SokratesAI/platform-config.git")],
        serving())
    assert status == 0
    assert any("every source is a git repository" in line for line in lines), lines


def test_a_multi_source_application_is_read_too():
    status, lines = check_with(
        [app("stack", sources=[
            {"repoURL": "https://github.com/SokratesAI/platform-config.git",
             "targetRevision": "main"},
            {"repoURL": "https://example.test/charts", "chart": "sealed-secrets",
             "targetRevision": "2.*"},
        ])],
        serving(**{"example.test": INDEX}))
    assert status == 0
    assert any("serves sealed-secrets" in line for line in lines), lines


def test_one_repo_is_fetched_once_however_many_applications_use_it():
    fetched = []

    def counting(request, timeout=None):
        fetched.append(request.full_url)
        return _Response(INDEX)

    status, _ = check_with(
        [app("a", "https://example.test/charts", "sealed-secrets"),
         app("b", "https://example.test/charts", "sealed-secrets")],
        counting)
    assert status == 0
    assert len(fetched) == 1, fetched


def check_with(items, opener):
    return helm_repo_health.check(runner=kubectl(items), opener=opener)
