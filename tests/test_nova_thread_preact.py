"""The chat thread keeps a row it already drew (issue #233, step 2; ADR 0010).

`thread.js` hands the rows `app.js` builds to Preact, keyed, so a poll that
brings one new message changes one row instead of emptying the thread and
closing whatever he had open. The behaviour tests load the shipped
`vendor/preact-htm.js` and `thread.js` into jsdom and skip when node or jsdom
is missing; set NOVA_JSDOM_NODE_PATH to a node_modules that has it.
"""

import json
import os
import shutil
import subprocess

import pytest

from agora_runner import nova_site

PUBLIC = os.path.join(os.path.dirname(nova_site.__file__), "nova_public")


def read(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as handle:
        return handle.read()


def test_both_scripts_are_served_and_load_before_app_js():
    assert nova_site.STATIC_ROUTES["/vendor/preact-htm.js"] == os.path.join("vendor", "preact-htm.js")
    assert nova_site.STATIC_ROUTES["/thread.js"] == "thread.js"
    page = read("index.html")
    assert page.index('src="/vendor/preact-htm.js"') < page.index('src="/thread.js"') < page.index('src="/app.js"')


def test_both_scripts_work_offline_and_move_the_update_banner():
    sw = read("sw.js")
    assert '"/vendor/preact-htm.js"' in sw and '"/thread.js"' in sw
    # thread.js is app code a stale tab runs; the pinned library is not in
    # the stamp for the reason echarts is not.
    assert "thread.js" in nova_site.SW_BUILD_INPUTS


def _jsdom_env():
    if shutil.which("node") is None:
        return None
    env = dict(os.environ)
    extra = os.environ.get("NOVA_JSDOM_NODE_PATH")
    if extra:
        env["NODE_PATH"] = extra
    probe = subprocess.run(["node", "-e", "require.resolve('jsdom')"], env=env, capture_output=True)
    return env if probe.returncode == 0 else None


HARNESS = r"""
const { JSDOM } = require("jsdom");
const fs = require("fs");
const dom = new JSDOM('<div id="t" class="ask-thread"></div>', { runScripts: "outside-only" });
const w = dom.window;
w.eval(fs.readFileSync(process.argv[1] + "/vendor/preact-htm.js", "utf8"));
w.eval(fs.readFileSync(process.argv[1] + "/thread.js", "utf8"));
const t = w.document.getElementById("t");
const out = {};
function node(text) { const d = w.document.createElement("div"); d.className = "ask-msg"; d.textContent = text; return d; }
function paint(rows) { w.novaThread.render(t, rows.map(([k, s, text]) => ({ key: k, sig: s, node: node(text) }))); }
function msgs() { return Array.from(t.querySelectorAll(".ask-msg")); }

paint([["a", "1", "first"], ["tail", NaN, "loader"]]);
const first = msgs()[0];
first.setAttribute("data-open", "yes");  // a drawer he opened
paint([["a", "1", "first"], ["b", "2", "second"], ["tail", NaN, "loader"]]);
out.keptNode = msgs()[0] === first;
out.keptOpen = msgs()[0].getAttribute("data-open");
out.afterPoll = msgs().map((m) => m.textContent);

paint([["a", "1b", "first, edited"], ["b", "2", "second"]]);
out.changedRow = msgs()[0].textContent;
out.changedRebuilt = msgs()[0] !== first;

const stray = node("sent bubble"); t.appendChild(stray);  // askPaintSent writes straight in
paint([["a", "1b", "first, edited"], ["b", "2", "second"]]);
out.strayGone = !t.contains(stray);
out.afterStray = msgs().map((m) => m.textContent);

paint([["d", "4", "one"], ["d", "5", "two"]]);
out.sameKey = msgs().map((m) => m.textContent);

t.textContent = "";  // the error path empties it by hand
paint([["c", "3", "fresh"]]);
out.afterEmptied = msgs().map((m) => m.textContent);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def thread():
    env = _jsdom_env()
    if env is None:
        pytest.skip("node with jsdom is not available")
    result = subprocess.run(["node", "-e", HARNESS, PUBLIC], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_a_poll_keeps_the_row_he_had_open(thread):
    assert thread["keptNode"] is True
    assert thread["keptOpen"] == "yes"
    assert thread["afterPoll"] == ["first", "second", "loader"]


def test_a_row_whose_content_changed_is_redrawn(thread):
    assert thread["changedRow"] == "first, edited"
    assert thread["changedRebuilt"] is True


def test_what_other_painters_left_behind_is_cleared(thread):
    assert thread["strayGone"] is True
    assert thread["afterStray"] == ["first, edited", "second"]


def test_a_thread_emptied_by_hand_is_drawn_again(thread):
    assert thread["afterEmptied"] == ["fresh"]


def test_two_rows_sharing_a_key_both_draw(thread):
    assert thread["sameKey"] == ["one", "two"]
