"""The digest bump must touch this service's own images and nothing else.

`.github/update-image-digest.py` replaces the `sed -i "s|image: .*|...|"` that
committed each build's digest into the paired `-config` repo. That sed matched
a *line*, not an image, so it rewrote every `image:` line in the manifest to
this repo's image whatever that line named -- a sidecar, an initContainer or a
second workload would have been silently retagged and deployed, with every
check green.

The equivalence test below is the one that made the swap safe to merge: on the
five real manifests in this org today the two produce byte-identical output,
so nothing live changes behaviour. The rest of the file pins the cases where
they differ, which is the whole point of the change.
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "update-image-digest.py"


def _module():
    spec = importlib.util.spec_from_file_location("update_image_digest", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


updater = _module()

OURS = "ghcr.io/sokratesai/agora-persona-runner"
NEW = OURS + "@sha256:" + "b" * 64
OLD = OURS + "@sha256:" + "a" * 64


def _sed(text, reference):
    """What the sed this replaces would have produced, for comparison."""
    out = []
    for line in text.splitlines(keepends=True):
        head, marker, _rest = line.partition("image: ")
        if marker:
            ending = "\n" if line.endswith("\n") else ""
            out.append(head + "image: " + reference + ending)
        else:
            out.append(line)
    return "".join(out)


TWO_WORKLOADS = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: runner
spec:
  template:
    spec:
      containers:
        - name: runner
          image: %s
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: site
spec:
  template:
    spec:
      containers:
        - name: site
          image: %s
""" % (OLD, OLD)


def test_both_of_our_workloads_are_bumped():
    updated, changed = updater.rewrite(TWO_WORKLOADS, NEW)
    assert updated.count(NEW) == 2
    assert [line for line, _old in changed] == [10, 21]


def test_it_agrees_with_the_sed_when_every_image_is_ours():
    updated, _changed = updater.rewrite(TWO_WORKLOADS, NEW)
    assert updated == _sed(TWO_WORKLOADS, NEW), (
        "on a manifest where every image is this repo's own, the replacement "
        "must be byte-identical to the sed it replaces -- that equivalence is "
        "what makes the swap safe on every config repo we have today"
    )


SIDECAR = """\
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      initContainers:
        - name: wait
          image: busybox:1.36
      containers:
        - name: runner
          image: %s
        - name: proxy
          image: ghcr.io/sokratesai/whatsapp-bridge@sha256:%s
""" % (OLD, "c" * 64)


def test_a_sidecar_is_left_alone():
    updated, changed = updater.rewrite(SIDECAR, NEW)
    assert "busybox:1.36" in updated
    assert "whatsapp-bridge@sha256:" + "c" * 64 in updated
    assert [old for _line, old in changed] == [OLD]


def test_the_sed_would_have_broken_that_manifest():
    # The precondition for the test above being worth anything: the tool it
    # replaces really does get this wrong, rather than the two agreeing.
    stomped = _sed(SIDECAR, NEW)
    assert "busybox:1.36" not in stomped
    assert stomped.count(NEW) == 3


def test_a_tagged_image_of_ours_is_still_ours():
    manifest = "spec:\n  containers:\n    - image: %s:v3\n" % (OURS,)
    updated, changed = updater.rewrite(manifest, NEW)
    assert NEW in updated
    assert changed == [(3, OURS + ":v3")]


def test_a_registry_port_is_not_read_as_a_tag():
    assert updater.repository_of("localhost:5000/nova") == "localhost:5000/nova"
    assert updater.repository_of("localhost:5000/nova:v2") == "localhost:5000/nova"


def test_matching_nothing_is_refused_rather_than_committed_unchanged():
    manifest = "spec:\n  containers:\n    - image: busybox:1.36\n"
    with pytest.raises(ValueError) as caught:
        updater.rewrite(manifest, NEW)
    assert "nothing to update" in str(caught.value)


def test_unparseable_yaml_is_refused():
    with pytest.raises(ValueError) as caught:
        updater.rewrite("spec:\n  - image: [unclosed\n", NEW)
    assert "could not parse" in str(caught.value)


def test_a_comment_holding_the_word_image_is_untouched():
    manifest = (
        "# both pods share one `image:` line, see below\n"
        "spec:\n  containers:\n    - image: %s\n" % (OLD,)
    )
    updated, _changed = updater.rewrite(manifest, NEW)
    assert updated.splitlines()[0] == "# both pods share one `image:` line, see below"


def test_a_trailing_comment_on_the_image_line_survives():
    manifest = "spec:\n  containers:\n    - image: %s  # pinned by CI\n" % (OLD,)
    updated, _changed = updater.rewrite(manifest, NEW)
    assert updated == "spec:\n  containers:\n    - image: %s  # pinned by CI\n" % (NEW,)


def test_main_refuses_and_leaves_the_file_alone(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    original = "spec:\n  containers:\n    - image: busybox:1.36\n"
    manifest.write_text(original)
    assert updater.main(["prog", str(manifest), NEW]) == 1
    assert manifest.read_text() == original, (
        "a refusal must not half-write the manifest -- the digest never "
        "reaching -config is the safe outcome, a partly rewritten one is not"
    )
