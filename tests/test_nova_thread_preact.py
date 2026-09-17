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

// Step 5: a send draws under the rows on screen; the old loader and a
// placeholder make way, and a row that stays keeps its node.
function appendSent() {
  w.novaThread.append(t, [{ key: "s", sig: "sent", node: node("sent") }, { key: "tail", sig: NaN, node: node("loader") }]);
}
paint([["a", "1", "first"], ["tail", NaN, "old loader"]]);
const before = msgs()[0];
appendSent();
out.sentAfterRows = msgs().map((m) => m.textContent);
out.sentKeptRow = msgs()[0] === before;
paint([["empty", "", "Ask me anything"]]);
appendSent();
out.sentOverEmpty = msgs().map((m) => m.textContent);
paint([["note", "loading…", "loading…"]]);
appendSent();
out.sentOverNote = msgs().map((m) => m.textContent);

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


def test_a_send_draws_under_the_rows_on_screen_and_replaces_the_loader(thread):
    assert thread["sentAfterRows"] == ["first", "sent", "loader"]
    assert thread["sentKeptRow"] is True


def test_a_send_replaces_a_placeholder_line(thread):
    assert thread["sentOverEmpty"] == ["sent", "loader"]
    assert thread["sentOverNote"] == ["sent", "loader"]


def test_two_rows_sharing_a_key_both_draw(thread):
    assert thread["sameKey"] == ["one", "two"]


# Step 3: the bubble itself is a Preact component (message.js). The helpers it
# needs are app.js's, handed over as window.novaChat; here they are fakes that
# record what they were asked, so the test pins the bubble's own structure --
# the same classes, in the same order, that askMessage builds by hand.
MESSAGE_HARNESS = r"""
const { JSDOM } = require("jsdom");
const fs = require("fs");
const dom = new JSDOM('<div id="t" class="ask-thread"></div>', { runScripts: "outside-only" });
const w = dom.window;
w.eval(fs.readFileSync(process.argv[1] + "/vendor/preact-htm.js", "utf8"));
w.eval(fs.readFileSync(process.argv[1] + "/message.js", "utf8"));
w.eval(fs.readFileSync(process.argv[1] + "/thread.js", "utf8"));
const calls = [];
let rich = 0;
w.novaChat = {
  owner: "Edvard",
  chatTime: (at) => (at ? "18:05" : ""),
  stepsLabel: (steps) => steps.length + " steps",
  stepMessageKey: (m) => "key-" + m.id,
  openStepSheet: (...a) => calls.push(["sheet", a[0], a[2], a[3]]),
  appendRichText: (node, cls, text) => { rich += 1; node.textContent = text; },
  askCopyButton: (text) => "copy:" + text,
  askRetryButton: (id, q) => "retry:" + q,
  openMessageActions: (actions) => calls.push(["actions", actions]),
};
const t = w.document.getElementById("t");
const out = {};
const shape = (n) => ({ cls: n.className, kids: Array.from(n.children).map((c) => c.className) });
function paint(rows) { w.novaThread.render(t, rows); }
const ask = { id: 1, sender: "Edvard", text: "status?", createdAt: "2026-09-17T16:05:00Z" };
const answer = { id: 2, sender: "Nova", text: "all green", createdAt: "2026-09-17T16:06:00Z", steps: [{}, {}] };
const loader = w.document.createElement("div"); loader.className = "ask-pending";
const rows = (a) => [
  { node: { message: ask, conversationId: "c1", limit: 50, retry: { question: "" } }, key: "1", sig: "s1" },
  { node: { message: a, conversationId: "c1", limit: 50, retry: { question: "status?" } }, key: "2", sig: JSON.stringify(a) },
  { node: { message: { id: 3, sender: "Nova", text: "", stepsOnly: true, steps: [{}] }, conversationId: "c1", limit: 50 }, key: "3", sig: "s3" },
  { node: loader, key: "tail", sig: NaN },
];
paint(rows(answer));
const kids = Array.from(t.firstChild.children);
out.rows = kids.map(shape);
out.mineWho = Array.from(kids[0].querySelector(".ask-who").children).map((c) => [c.className, c.textContent]);
out.theirName = kids[1].querySelector(".ask-who-name").textContent;
out.text = kids[1].querySelector(".ask-text").textContent;
out.stepsLabel = kids[1].querySelector(".ask-steps").getAttribute("aria-label");
kids[1].querySelector(".ask-steps").click();
kids[1].querySelector(".ask-more").click();
kids[0].querySelector(".ask-more").click();
out.calls = calls.slice();
const first = kids[1];
first.setAttribute("data-open", "yes");
const richBefore = rich;
paint(rows(answer));
out.keptNode = t.firstChild.children[1] === first && first.getAttribute("data-open") === "yes";
out.richRedrawnOnSamePoll = rich - richBefore;
// Same text, different sig (the answer stopped being partial): the bubble is
// drawn again, the rich text is not rebuilt.
const richBeforeFlag = rich;
paint(rows(Object.assign({}, answer, { partial: false })));
out.richRedrawnOnSameText = rich - richBeforeFlag;
out.textAfterFlag = t.firstChild.children[1].querySelector(".ask-text").textContent;
paint(rows(Object.assign({}, answer, { text: "all green, and done" })));
out.editedText = t.firstChild.children[1].querySelector(".ask-text").textContent;
paint([{ node: { message: { id: 9, sender: "Nova", text: "", createdAt: "" }, conversationId: "c1" }, key: "9", sig: "x" }]);
out.bare = shape(t.firstChild.children[0]);
// An answer with nothing asked above it has no question to re-ask.
calls.length = 0;
paint([{ node: { message: { id: 10, sender: "Nova", text: "hello" }, conversationId: "c1", retry: { question: "" } }, key: "10", sig: "y" }]);
t.firstChild.children[0].querySelector(".ask-more").click();
out.noQuestion = calls;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def bubbles():
    env = _jsdom_env()
    if env is None:
        pytest.skip("node with jsdom is not available")
    result = subprocess.run(["node", "-e", MESSAGE_HARNESS, PUBLIC], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_message_js_is_served_offline_and_loads_between_preact_and_thread_js():
    assert nova_site.STATIC_ROUTES["/message.js"] == "message.js"
    assert "message.js" in nova_site.SW_BUILD_INPUTS
    assert '"/message.js"' in read("sw.js")
    page = read("index.html")
    assert page.index('src="/vendor/preact-htm.js"') < page.index('src="/message.js"') < page.index('src="/app.js"')


def test_app_js_hands_messages_to_the_component_and_its_helpers_over():
    app = read("app.js")
    assert "window.novaMessage && window.novaThread ? { message: message" in app
    for name in ("chatTime", "stepsLabel", "openStepSheet", "stepMessageKey", "appendRichText",
                 "askCopyButton", "askRetryButton", "openMessageActions"):
        assert name + ": " + name in app


def test_the_bubble_has_the_hand_built_structure(bubbles):
    assert bubbles["rows"] == [
        {"cls": "ask-msg ask-mine", "kids": ["ask-who", "ask-text", "ask-more"]},
        {"cls": "ask-msg ask-theirs", "kids": ["ask-who", "ask-steps", "ask-text", "ask-more"]},
        {"cls": "ask-msg-steps", "kids": ["ask-steps"]},
        # a DOM row (the loader) still sits in its slot
        {"cls": "thread-slot", "kids": ["ask-pending"]},
    ]
    assert bubbles["mineWho"] == [["ask-when", "18:05"], ["ask-who-name", "You"]]
    assert bubbles["theirName"] == "Nova"
    assert bubbles["text"] == "all green"
    assert bubbles["stepsLabel"] == "2 steps — open the details"


def test_an_undated_message_with_nothing_to_copy_gets_no_stamp_and_no_button(bubbles):
    assert bubbles["bare"] == {"cls": "ask-msg ask-theirs", "kids": ["ask-who", "ask-text"]}


def test_steps_and_actions_open_what_they_did(bubbles):
    sheet, answer_actions, own_actions = bubbles["calls"]
    assert sheet == ["sheet", "c1", 50, "key-2"]
    assert answer_actions == ["actions", ["copy:all green", "retry:status?"]]
    # His own line has text to copy and nothing to re-ask.
    assert own_actions == ["actions", ["copy:status?"]]


def test_a_poll_keeps_the_bubble_and_does_not_rebuild_its_text(bubbles):
    assert bubbles["keptNode"] is True
    assert bubbles["richRedrawnOnSamePoll"] == 0
    assert bubbles["richRedrawnOnSameText"] == 0
    assert bubbles["textAfterFlag"] == "all green"
    assert bubbles["editedText"] == "all green, and done"


def test_an_answer_with_no_question_above_it_offers_no_re_ask(bubbles):
    assert bubbles["noQuestion"] == [["actions", ["copy:hello"]]]


# Step 4: the bottom of the thread -- the loader and the lost-turn card -- is
# drawn by message.js too, so a poll keeps its nodes instead of swapping them.
TAIL_HARNESS = r"""
const { JSDOM } = require("jsdom");
const fs = require("fs");
const dom = new JSDOM('<div id="t" class="ask-thread"></div>', { runScripts: "outside-only" });
const w = dom.window;
w.eval(fs.readFileSync(process.argv[1] + "/vendor/preact-htm.js", "utf8"));
w.eval(fs.readFileSync(process.argv[1] + "/message.js", "utf8"));
w.eval(fs.readFileSync(process.argv[1] + "/thread.js", "utf8"));
const made = { orbit: 0, retry: [] };
w.novaChat = {
  owner: "Edvard",
  pendingClockAfter: 20,
  askPendingSeconds: (at) => (at ? 75 : null),
  askElapsed: () => "1m 15s",
  askOrbit: () => { made.orbit += 1; const o = w.document.createElement("div"); o.className = "ask-orbit"; return o; },
  askRetryButton: (id, q) => { made.retry.push([id, q]); const b = w.document.createElement("button"); b.className = "ask-retry"; b.textContent = "Ask again"; return b; },
};
const t = w.document.getElementById("t");
const out = {};
const tail = (props) => w.novaThread.render(t, [{ node: Object.assign({ conversationId: "c1" }, props), key: "tail", sig: NaN }]);
const row = () => t.firstChild.children[0];
const tree = (n) => ({ cls: n.className, kids: Array.from(n.children).map(tree) });

tail({ tail: "pending", progress: {} });
out.loader = tree(row());
const orbit = t.querySelector(".ask-orbit");
tail({ tail: "pending", progress: {} });
out.orbitKept = t.querySelector(".ask-orbit") === orbit;

tail({ tail: "pending", progress: { askedAt: "x", steps: 3, latest: { capability: "vault_read", detail: "notes.md" } } });
out.working = tree(row());
out.workingText = [".ask-pending-head", ".ask-pending-tool", ".ask-pending-detail", ".ask-pending-count"]
  .map((s) => t.querySelector(s).textContent);

tail({ tail: "lost", quietSeconds: 720, question: "status?" });
out.lost = tree(row());
const button = t.querySelector(".ask-retry");
button.disabled = true; button.textContent = "sending…";  // he tapped it
tail({ tail: "lost", quietSeconds: 750, question: "status?" });  // 12.5 rounds, as askLost does
out.buttonKept = t.querySelector(".ask-retry") === button && button.disabled && button.textContent === "sending…";
out.lostText = t.querySelector(".ask-stopped").textContent;
tail({ tail: "lost", quietSeconds: 780, question: "" });
out.noQuestion = tree(row());
out.made = made;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def tails():
    env = _jsdom_env()
    if env is None:
        pytest.skip("node with jsdom is not available")
    result = subprocess.run(["node", "-e", TAIL_HARNESS, PUBLIC], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_app_js_hands_the_tail_to_the_component():
    app = read("app.js")
    assert '{ tail: lost ? "lost" : "pending"' in app
    for name in ("askPendingSeconds", "askElapsed", "askOrbit"):
        assert name + ": " + name in app
    assert "pendingClockAfter: PENDING_CLOCK_AFTER_SECONDS" in app


def test_the_loader_has_the_hand_built_structure_and_keeps_its_orbit(tails):
    orbit = {"cls": "thread-slot", "kids": [{"cls": "ask-orbit", "kids": []}]}
    assert tails["loader"] == {"cls": "ask-msg ask-theirs ask-pending", "kids": [orbit]}
    assert tails["orbitKept"] is True


def test_a_working_turn_shows_its_clock_newest_tool_and_step_count(tails):
    assert [k["cls"] for k in tails["working"]["kids"]] == ["ask-pending-head", "ask-pending-step", "ask-pending-count"]
    assert tails["workingText"] == ["1m 15s", "vault_read", "notes.md", "3 steps so far"]


def test_a_lost_turn_keeps_an_ask_again_he_already_tapped(tails):
    assert tails["lost"] == {"cls": "ask-msg ask-theirs ask-stopped-row", "kids": [
        {"cls": "ask-stopped", "kids": []},
        {"cls": "thread-slot", "kids": [{"cls": "ask-retry", "kids": []}]}]}
    assert tails["buttonKept"] is True
    assert tails["lostText"] == "No answer came back. Nothing has arrived for 13 minutes, so the turn was lost."


def test_a_lost_turn_with_nothing_asked_offers_no_button(tails):
    assert tails["noQuestion"]["kids"] == [{"cls": "ask-stopped", "kids": []}]
    # One orbit and one button were ever built across all those polls.
    assert tails["made"] == {"orbit": 1, "retry": [["c1", "status?"]]}
