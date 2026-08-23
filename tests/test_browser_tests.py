"""The decision `tools.browser_tests` makes before it runs anything.

Cycle 351. The bug this guards against is not "the tests fail" -- it is a
cycle concluding it cannot run the browser suite at all, which is what
happened when the shared `node_modules` was linked one directory too high.
"""

from pathlib import Path

from tools.browser_tests import has_jsdom, main_worktree, plan, provision


def _modules_with_jsdom(root: Path) -> Path:
    mods = root / "node_modules"
    (mods / "jsdom").mkdir(parents=True)
    return mods


def test_main_worktree_is_the_parent_of_the_common_git_dir():
    assert main_worktree("/data/workspace/agora-persona-runner/.git") == Path(
        "/data/workspace/agora-persona-runner"
    )


def test_already_installed_needs_no_work(tmp_path):
    browser = tmp_path / "tests" / "browser"
    browser.mkdir(parents=True)
    _modules_with_jsdom(browser)
    action, target = plan(browser, None)
    assert action == "present"
    assert target == str(browser / "node_modules")


def test_empty_worktree_links_the_shared_modules(tmp_path):
    browser = tmp_path / "worktree" / "tests" / "browser"
    browser.mkdir(parents=True)
    shared_root = tmp_path / "shared" / "tests" / "browser"
    shared_root.mkdir(parents=True)
    shared = _modules_with_jsdom(shared_root)

    action, target = plan(browser, shared)
    assert action == "link"
    assert target == str(shared)


def test_no_shared_checkout_falls_back_to_install(tmp_path):
    browser = tmp_path / "tests" / "browser"
    browser.mkdir(parents=True)
    assert plan(browser, None) == ("install", str(browser))


def test_shared_modules_without_jsdom_is_not_worth_linking(tmp_path):
    """The root `node_modules` is the wrong one and looks like the right one.

    This is the exact confusion that made Cycle 349 report the suite
    unrunnable: a populated `node_modules` that happens to hold no jsdom.
    """
    browser = tmp_path / "tests" / "browser"
    browser.mkdir(parents=True)
    shared = tmp_path / "root" / "node_modules"
    (shared / "express").mkdir(parents=True)

    assert plan(browser, shared) == ("install", str(browser))


def test_a_half_finished_install_is_planned_for_reinstall_not_a_link(tmp_path):
    """Linking would mean deleting a real directory somebody else's cycle owns."""
    browser = tmp_path / "tests" / "browser"
    (browser / "node_modules" / "chalk").mkdir(parents=True)
    shared_root = tmp_path / "shared" / "tests" / "browser"
    shared_root.mkdir(parents=True)
    shared = _modules_with_jsdom(shared_root)

    assert plan(browser, shared) == ("install", str(browser))


def test_a_dangling_symlink_is_planned_as_a_link(tmp_path):
    """A previous cycle's worktree is gone; its symlink survives in ours."""
    browser = tmp_path / "tests" / "browser"
    browser.mkdir(parents=True)
    (browser / "node_modules").symlink_to(tmp_path / "gone" / "node_modules")
    shared_root = tmp_path / "shared" / "tests" / "browser"
    shared_root.mkdir(parents=True)
    shared = _modules_with_jsdom(shared_root)

    assert plan(browser, shared) == ("link", str(shared))


def test_has_jsdom_wants_a_directory_not_a_name(tmp_path):
    mods = tmp_path / "node_modules"
    mods.mkdir()
    assert not has_jsdom(mods)
    (mods / "jsdom").mkdir()
    assert has_jsdom(mods)


# `plan` decides and `provision` acts, and the reviewer's finding on Cycle 351
# was that every test above stopped at the decision while carrying a name that
# promised the act. These four run `provision` itself. `npm ci` is deliberately
# not among them -- it wants the network and two seconds, and what it does is
# npm's business, not this module's.


def test_provision_creates_the_link_it_planned(tmp_path):
    browser = tmp_path / "tests" / "browser"
    browser.mkdir(parents=True)
    shared = _modules_with_jsdom(tmp_path / "shared")

    provision(browser, "link", str(shared))

    mine = browser / "node_modules"
    assert mine.is_symlink()
    assert has_jsdom(mine)


def test_provision_replaces_a_dangling_link_rather_than_erroring(tmp_path):
    browser = tmp_path / "tests" / "browser"
    browser.mkdir(parents=True)
    (browser / "node_modules").symlink_to(tmp_path / "gone" / "node_modules")
    shared = _modules_with_jsdom(tmp_path / "shared")

    provision(browser, "link", str(shared))

    assert has_jsdom(browser / "node_modules")


def test_provision_replaces_a_link_that_points_at_the_wrong_place(tmp_path):
    """The shared checkout moved; a stale link to the old one still resolves."""
    browser = tmp_path / "tests" / "browser"
    browser.mkdir(parents=True)
    stale = tmp_path / "stale" / "node_modules"
    (stale / "express").mkdir(parents=True)
    (browser / "node_modules").symlink_to(stale)
    shared = _modules_with_jsdom(tmp_path / "shared")

    provision(browser, "link", str(shared))

    assert has_jsdom(browser / "node_modules")


def test_provision_does_nothing_at_all_when_the_plan_is_present(tmp_path):
    """`present` must not touch the directory -- another cycle may be reading it."""
    browser = tmp_path / "tests" / "browser"
    browser.mkdir(parents=True)
    mine = _modules_with_jsdom(browser)
    (mine / "sentinel").mkdir()

    provision(browser, "present", str(mine))

    assert (mine / "sentinel").is_dir()
    assert not mine.is_symlink()
