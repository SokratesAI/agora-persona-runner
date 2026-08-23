"""The decision `tools.browser_tests` makes before it runs anything.

Cycle 351. The bug this guards against is not "the tests fail" -- it is a
cycle concluding it cannot run the browser suite at all, which is what
happened when the shared `node_modules` was linked one directory too high.
"""

from pathlib import Path

from tools.browser_tests import has_jsdom, main_worktree, plan


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


def test_a_half_finished_install_is_reinstalled_not_linked_over(tmp_path):
    """Linking would mean deleting a real directory somebody else's cycle owns."""
    browser = tmp_path / "tests" / "browser"
    (browser / "node_modules" / "chalk").mkdir(parents=True)
    shared_root = tmp_path / "shared" / "tests" / "browser"
    shared_root.mkdir(parents=True)
    shared = _modules_with_jsdom(shared_root)

    assert plan(browser, shared) == ("install", str(browser))


def test_a_dangling_symlink_is_replaced_by_a_good_link(tmp_path):
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
