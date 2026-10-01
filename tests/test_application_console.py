"""A DOM shim just large enough to run the console's real JavaScript.

The copy button is the interaction this page exists to get right, and asserting on the generated HTML
string cannot show whether clicking H2's button copies H2. So the actual `<script>` body out of
`console.py` is executed against this shim, the real render path is driven, and the value that reaches
the clipboard is read back.

`quickjs` is a test-time dependency only: it is not in `pyproject.toml` and nothing in `src/` imports
it. The tests skip when it is absent, exactly as the google-adk tests do.
"""

from __future__ import annotations

import json
import re
import unittest

try:
    import quickjs
except ImportError:  # pragma: no cover - exercised only where the engine is absent
    quickjs = None

from debugagent.application.console import CONSOLE_HTML

SCRIPT = CONSOLE_HTML.split("<script>")[1].split("</script>")[0]

#: A DOM with the pieces the console actually touches. Deliberately small: every method here exists
#: because the console calls it, which keeps the shim honest about what is being exercised.
SHIM = r"""
var __log = { copied: [], exec: 0, posts: [] };
var __ids = {};

function Node(tag) {
  this.tagName = String(tag).toUpperCase();
  this.childNodes = [];
  this.parentNode = null;
  this.attributes = {};
  this.dataset = {};
  this.style = { cssText: "" };
  this._text = "";
  this._id = "";
  this._listeners = {};
  this.className = "";
  this.value = "";
  this.hidden = false;
  this.open = false;
  this.selected = false;
}
/* `id` is an accessor so a node that sets `.id = "decision"` - which is how the console does it - is
   reachable through getElementById, exactly as in a browser. */
Object.defineProperty(Node.prototype, "id", {
  get: function () { return this._id; },
  set: function (value) { this._id = String(value); __ids[this._id] = this; }
});
Object.defineProperty(Node.prototype, "textContent", {
  get: function () {
    if (this.childNodes.length) {
      return this.childNodes.map(function (c) { return c.textContent; }).join("");
    }
    return this._text;
  },
  set: function (value) { this.childNodes = []; this._text = String(value); }
});
Object.defineProperty(Node.prototype, "firstChild", {
  get: function () { return this.childNodes[0] || null; }
});
Object.defineProperty(Node.prototype, "lastChild", {
  get: function () { return this.childNodes[this.childNodes.length - 1] || null; }
});
Node.prototype.append = function () {
  for (var i = 0; i < arguments.length; i++) {
    arguments[i].parentNode = this;
    this.childNodes.push(arguments[i]);
  }
};
/* `appendChild` is a distinct name in the DOM, not an alias of `append`; the clipboard fallback uses
   it, and a shim that only had `append` turned a real failure into a silently rejected promise. */
Node.prototype.appendChild = function (child) {
  child.parentNode = this;
  this.childNodes.push(child);
  return child;
};
Node.prototype.removeChild = function (child) {
  var index = this.childNodes.indexOf(child);
  if (index >= 0) this.childNodes.splice(index, 1);
  child.parentNode = null;
};
Node.prototype.insertBefore = function (child, before) {
  var index = before ? this.childNodes.indexOf(before) : this.childNodes.length;
  child.parentNode = this;
  this.childNodes.splice(index < 0 ? this.childNodes.length : index, 0, child);
  return child;
};
Node.prototype.remove = function () {
  if (this.parentNode) this.parentNode.removeChild(this);
};
Node.prototype.setAttribute = function (name, value) {
  this.attributes[name] = String(value);
  if (name === "id") this.id = String(value);
  if (name === "readonly") this.readOnly = true;
};
/* In a DOM, `el.type = "button"` reflects to the content attribute, and the console sets properties
   rather than attributes. Without this the accessibility assertions would read a missing attribute
   that a browser would report. */
["type", "value", "title", "htmlFor", "class"].forEach(function (name) {
  Object.defineProperty(Node.prototype, name, {
    get: function () { return this["_" + name]; },
    set: function (v) { this["_" + name] = String(v); this.attributes[name] = String(v); }
  });
});
Node.prototype.getAttribute = function (name) {
  if (name in this.attributes) return this.attributes[name];
  if (name === "id") return this.id;
  return null;
};
Node.prototype.hasAttribute = function (name) { return name in this.attributes; };
Node.prototype.addEventListener = function (type, fn) {
  (this._listeners[type] = this._listeners[type] || []).push(fn);
};
Node.prototype.click = function () {
  var list = this._listeners["click"] || [];
  for (var i = 0; i < list.length; i++) list[i].call(this, { target: this });
};
Node.prototype.select = function () { this.selected = true; };
Node.prototype.setSelectionRange = function (start, end) { this.range = [start, end]; };
Node.prototype.querySelector = function (selector) { return findAll(this, selector)[0] || null; };
Node.prototype.querySelectorAll = function (selector) { return findAll(this, selector); };

function walk(node, out) {
  for (var i = 0; i < node.childNodes.length; i++) {
    out.push(node.childNodes[i]);
    walk(node.childNodes[i], out);
  }
  return out;
}
function matches(node, selector) {
  if (selector.charAt(0) === ".") {
    return node.className.split(/\s+/).indexOf(selector.slice(1)) >= 0;
  }
  if (selector.charAt(0) === "#") return node.id === selector.slice(1);
  return node.tagName === selector.toUpperCase();
}
function findAll(root, selector) {
  var pool = walk(root, []);
  var parts = selector.trim().split(/\s+/);
  for (var p = 0; p < parts.length; p++) {
    var next = [];
    for (var i = 0; i < pool.length; i++) {
      /* A descendant selector must descend into each match before testing the next part, or
         ".events li" silently matches nothing. */
      if (matches(pool[i], parts[p])) {
        next.push(pool[i]);
        if (p < parts.length - 1) walk(pool[i], next);
      }
    }
    pool = next;
  }
  return pool;
}

var document = {
  body: new Node("body"),
  createElement: function (tag) { return new Node(tag); },
  createTextNode: function (text) { var n = new Node("#text"); n.textContent = String(text);
                                    return n; },
  getElementById: function (id) { return __ids[id] || null; },
  querySelector: function (s) { return document.body.querySelector(s); },
  querySelectorAll: function (s) { return document.body.querySelectorAll(s); },
  getSelection: function () { return null; },
  execCommand: function (command) {
    /* The fallback path copies whatever scratch textarea is currently selected, which is exactly the
       string the button closed over. */
    if (command !== "copy") return false;
    var pool = walk(document.body, []).filter(function (n) { return n.selected; });
    if (!pool.length) return false;
    __log.copied.push(pool[0].value);
    __log.exec += 1;
    return true;
  }
};
var window = { isSecureContext: false };
var navigator = {};

/* The console calls a bare `fetch`, so it must exist as a global. Recording it here is also what lets a
   test read back exactly what the page would have sent. */
function fetch(url, options) {
  __log.posts.push({ url: String(url), body: options ? options.body : null });
  return Promise.resolve({ ok: true, status: 200,
                          json: function () { return Promise.resolve({}); } });
}

/* Timers are inert: the copy button paints its "Copied" state synchronously, and a live timer would only
   add a scheduled poll() the shim cannot serve. */
function setTimeout() { return 0; }
function clearTimeout() {}
"""


def _build() -> "quickjs.Context":
    """A context with the shim, the console's own script, and the elements it looks up by id."""
    context = quickjs.Context()
    context.eval(SHIM)
    context.eval("""
      ["m-session", "m-status", "m-conn", "start", "start-btn", "description", "banner", "case",
       "case-title", "chips", "symptoms", "timeline", "left", "right", "live", "copy-live"]
        .forEach(function (id) { var n = document.createElement("div"); n.id = id;
                                 document.body.append(n); });
    """)
    context.eval(SCRIPT)
    return context


def _run(context, expression: str):
    """Evaluate JS for effect, or return a primitive."""
    return context.eval(expression)


def _data(context, expression: str):
    """Evaluate JS that returns a structure and get it back as Python.

    quickjs hands a JS array back as an opaque object, so every structured read goes through
    `JSON.stringify` rather than pretending the bridge converts them.
    """
    return json.loads(context.eval(f"JSON.stringify({expression})"))


def _drain(context, rounds: int = 50) -> None:
    """Run pending microtasks so an `async` click handler finishes.

    The copy button's handler is `async` and awaits `copyText`; without draining the job queue the
    assertion would run before the clipboard was ever touched, which is exactly the kind of false pass
    a synchronous-looking test would give.
    """
    for _ in range(rounds):
        if not context.execute_pending_job():
            return


def _load(context, snapshot: dict) -> None:
    """Drive the console's render path with a snapshot, forcing a rebuild."""
    _run(context, f"render({json.dumps(snapshot)}, true);")
    _drain(context)


def _poll(context, snapshot: dict) -> None:
    """Deliver a snapshot the way a poll does: through the change check, not a forced rebuild."""
    _run(context, f"render({json.dumps(snapshot)}, false);")
    _drain(context)


def _panel_names(context) -> list:
    return _data(context, """
      document.body.querySelectorAll(".panel").map(function (p) { return p.dataset.panel; })
    """)


def _in_panel(context, name: str, body: str):
    """Run `body(panelNode)` against the named panel, in JS."""
    return _data(context, f"""
      (function () {{
        var panels = document.body.querySelectorAll(".panel");
        for (var i = 0; i < panels.length; i++)
          if (panels[i].dataset.panel === {json.dumps(name)}) return panels[i];
        return null;
      }})()
    """) is not None


def _hypotheses(context) -> list:
    """The Reasoning panel's cards, in document order, with their copy controls described."""
    return _data(context, """
      (function () {
        var panels = document.body.querySelectorAll(".panel");
        var target = null;
        for (var i = 0; i < panels.length; i++)
          if (panels[i].dataset.panel === "Reasoning") target = panels[i];
        if (!target) return [];
        function text(node, selector) {
          var found = node.querySelector(selector);
          return found ? found.textContent : null;
        }
        return target.querySelectorAll(".card").map(function (card) {
          var button = card.querySelector("button");
          return {
            ref: text(card, ".cid"),
            text: text(card, ".ctext"),
            copyLabel: button ? button.getAttribute("aria-label") : null,
            copyText: button ? button.dataset.copyText : null,
            copyKey: button ? button.dataset.key : null
          };
        });
      })()
    """)


def _click_hypothesis(context, ref: str) -> None:
    """Click that hypothesis's own copy button, through the real listener."""
    _run(context, f"""
      (function () {{
        var panels = document.body.querySelectorAll(".panel");
        for (var i = 0; i < panels.length; i++) {{
          if (panels[i].dataset.panel !== "Reasoning") continue;
          var cards = panels[i].querySelectorAll(".card");
          for (var c = 0; c < cards.length; c++) {{
            var id = cards[c].querySelector(".cid");
            if (id && id.textContent === {json.dumps(ref)}) cards[c].querySelector("button").click();
          }}
        }}
      }})()
    """)
    _drain(context)


def _state_claim(context, which: str = "Supports it") -> None:
    """Answer the required claim question, which is what reveals the Accept/Modify/Reject actions."""
    _run(context, f"""
      (function () {{
        var buttons = document.getElementById("decision").querySelectorAll("button");
        for (var i = 0; i < buttons.length; i++)
          if (buttons[i].textContent === {json.dumps(which)}) buttons[i].click();
      }})()
    """)
    _drain(context)


def _decisions(context) -> list:
    return _data(context, """
      (function () {
        return document.getElementById("decision").querySelectorAll("button")
          .filter(function (b) { return !!b.dataset.decision; })
          .map(function (b) { return b.dataset.decision; });
      })()
    """)


def _copied(context) -> list:
    """Everything the clipboard has been handed since the last drain."""
    value = _data(context, "__log.copied")
    _run(context, "__log.copied = [];")
    return value


def _copy_buttons(context) -> list:
    return _data(context, """
      document.body.querySelectorAll("button")
        .filter(function (b) { return !!b.dataset.key; })
        .map(function (b) {
          return { label: b.getAttribute("aria-label"), key: b.dataset.key,
                   text: b.dataset.copyText, word: b.lastChild.textContent,
                   copied: b.dataset.copied };
        })
    """)


H1 = "Invoice creation is not idempotent for retries, so a retried job writes a second row."
H2 = "The retry mechanism allows the same job to execute concurrently in two workers."
H3 = "The database does not enforce uniqueness on the billing reference column."

SNAPSHOT = {
    "session_id": "s1",
    "status": "running",
    "outcome": "running",
    "error": "",
    "pending": {
        "kind": "decide", "session_id": "s1", "token": "decide:H1",
        "hypothesis_ref": "H1", "statement": H1,
        "mismatched": ["proxy"], "missing": [], "options": ["accept", "modify", "reject"],
    },
    "view": {
        "session_id": "s1",
        "title": "billing-service retry duplicate invoice",
        "status": "running",
        "case": {
            "signature": "billing-service retry duplicate invoice",
            "symptoms": ["two invoice rows for one retried job"],
            "environment": [
                {"name": "service", "value": "billing-service", "stated": True},
                {"name": "runtime", "value": None, "stated": False},
                {"name": "proxy", "value": None, "stated": False},
                {"name": "region", "value": "eu-west-1", "stated": True},
            ],
        },
        "panels": [
            {"name": "Memory", "trust": "KNOWLEDGE", "items": [
                {"ref": "seed-001", "label": "seed-001", "value": "relevant",
                 "detail": "outcome: resolved | environment: proxy=nginx-1.25, service=billing-service"
                           " | recalled because: same duplicate invoice signature",
                 "source": "memory"}],
             "note": "Memory abstained: no past case was close enough to act on."},
            {"name": "Evidence", "trust": "EVIDENCE", "items": [
                {"label": "service", "value": "billing-service", "source": "engineer"},
                {"label": "proxy", "value": None, "source": "engineer"}],
             "note": "Source of every item is shown. Not stated by the engineer: proxy, runtime."},
            {"name": "Reasoning", "trust": "PROPOSAL", "items": [
                {"ref": "H1", "label": H1, "value": "conditional",
                 "detail": "relevance: conditional | cited: seed-001 | next step: add a unique index"
                           " | would be refuted if: duplicates stop after a retry",
                 "source": "agent"},
                {"ref": "H2", "label": H2, "value": "weak-reference",
                 "detail": "relevance: weak-reference | cited: seed-001 | next step: serialise retries"
                           " | would be refuted if: both workers never overlap",
                 "source": "agent"},
                {"ref": "H3", "label": H3, "value": "generic",
                 "detail": "relevance: generic | cited: none | next step: inspect the index"
                           " | would be refuted if: a unique index already exists",
                 "source": "agent"}],
             "note": "Candidate explanations produced by the agent. None has been verified yet."},
            {"name": "Validation", "trust": "EVIDENCE", "items": [
                {"ref": "H1", "label": "insufficient_evidence", "value": "accept",
                 "detail": "no note recorded", "source": "engineer"},
                {"ref": "H1", "label": "environment in conflict", "value": "proxy",
                 "detail": "fields the cited past case recorded differently from, or not at all in,"
                           " current evidence", "source": "engineer", "trust": "OBSERVATION"}],
             "note": "Each hypothesis is listed with the status the gate reached."},
            {"name": "Trace", "trust": "TRACE", "items": [
                {"ref": "T01", "label": "normalized: billing-service retry duplicate invoice",
                 "source": "system"},
                {"ref": "T02", "label": "recalled 1 candidate(s)", "source": "system"}],
             "note": "The order stages ran."},
        ],
    },
}


@unittest.skipIf(quickjs is None, "quickjs is not importable here")
class ConsoleInteraction(unittest.TestCase):
    """The console's real JavaScript, driven through its real render path."""

    def setUp(self):
        self.ctx = _build()
        _load(self.ctx, SNAPSHOT)

    # -- hypotheses and copy ------------------------------------------------

    def test_every_hypothesis_renders_as_its_own_card(self):
        cards = _hypotheses(self.ctx)
        self.assertEqual([card["ref"] for card in cards], ["H1", "H2", "H3"])
        self.assertEqual([card["text"] for card in cards], [H1, H2, H3])

    def test_each_hypothesis_has_its_own_named_copy_action(self):
        cards = _hypotheses(self.ctx)
        self.assertEqual([card["copyLabel"] for card in cards],
                         ["Copy hypothesis H1", "Copy hypothesis H2", "Copy hypothesis H3"])
        self.assertEqual(len({card["copyKey"] for card in cards}), 3,
                         "the copy buttons share a key, so they could share a copied state")

    def test_each_copy_action_targets_its_own_hypothesis_text(self):
        for card in _hypotheses(self.ctx):
            with self.subTest(ref=card["ref"]):
                self.assertEqual(card["copyText"], card["text"])
                self.assertNotIn(card["ref"], card["copyText"],
                                 "the identifier leaked into the copied text")
                self.assertNotIn("<", card["copyText"], "HTML was copied instead of text")

    def test_clicking_h2_copies_only_h2(self):
        """The reported bug: H2 could not be copied. Clicking H2 must copy H2 and nothing else."""
        _click_hypothesis(self.ctx, "H2")
        copied = _copied(self.ctx)
        self.assertEqual(len(copied), 1, f"expected exactly one copy, got {copied}")
        self.assertEqual(copied[0], H2)
        self.assertNotIn("H1", copied[0])
        self.assertNotIn("H3", copied[0])

    def test_copying_h1_and_h3_still_copies_their_own_text(self):
        for ref, text in (("H1", H1), ("H3", H3)):
            with self.subTest(ref=ref):
                _click_hypothesis(self.ctx, ref)
                copied = _copied(self.ctx)
                self.assertTrue(copied, f"{ref} was not copied")
                self.assertEqual(copied[-1], text)

    def test_a_copied_button_says_so_and_only_that_button_does(self):
        _click_hypothesis(self.ctx, "H2")
        buttons = {button["key"]: button for button in _copy_buttons(self.ctx)
                   if button["key"].startswith("hyp:")}
        self.assertEqual(buttons["hyp:H2"]["word"], "Copied")
        self.assertEqual(buttons["hyp:H2"]["copied"], "1")
        self.assertEqual(buttons["hyp:H1"]["word"], "Copy")
        self.assertEqual(buttons["hyp:H3"]["word"], "Copy")

    def test_copy_feedback_is_announced_to_a_screen_reader(self):
        """Not colour alone: the change is also text and an aria-live announcement."""
        _click_hypothesis(self.ctx, "H2")
        self.assertIn("Copied hypothesis H2",
                      _run(self.ctx, "document.getElementById('copy-live').textContent"))
        self.assertIn('role="status"', CONSOLE_HTML)
        self.assertIn('aria-live="polite"', CONSOLE_HTML)

    def test_the_fallback_path_copies_the_text_when_the_clipboard_api_is_absent(self):
        """`navigator.clipboard` is gated on a secure context; the console must still copy on plain HTTP."""
        _click_hypothesis(self.ctx, "H3")
        self.assertEqual(_copied(self.ctx), [H3])
        self.assertGreater(_run(self.ctx, "__log.exec"), 0, "the fallback never ran")
        self.assertIn("execCommand", SCRIPT)
        self.assertIn("navigator.clipboard", SCRIPT)

    def test_long_hypothesis_text_survives_intact(self):
        long_text = "x" * 4000 + " and a very long tail " + "y" * 2000
        payload = json.loads(json.dumps(SNAPSHOT))
        payload["view"]["panels"][2]["items"][1]["label"] = long_text
        _load(self.ctx, payload)
        cards = _hypotheses(self.ctx)
        self.assertEqual(cards[1]["text"], long_text)
        self.assertEqual(cards[1]["copyText"], long_text)
        _click_hypothesis(self.ctx, "H2")
        self.assertEqual(_copied(self.ctx), [long_text])

    # -- panels, trust, absence --------------------------------------------

    def test_absent_panels_are_not_rendered(self):
        """No worker stage ran, so there is no Workers panel and none is invented."""
        names = _panel_names(self.ctx)
        self.assertNotIn("Workers", names)
        for expected in ("Memory", "Evidence", "Reasoning", "Validation", "Trace", "Decision"):
            self.assertIn(expected, names)

    def test_every_rendered_panel_declares_its_trust_level(self):
        found = _data(self.ctx, """
          (function () {
            var out = {};
            var panels = document.body.querySelectorAll(".panel");
            for (var i = 0; i < panels.length; i++) {
              var labels = panels[i].querySelectorAll(".trust");
              out[panels[i].dataset.panel] = labels.length ? labels[0].textContent : null;
            }
            return out;
          })()
        """)
        self.assertEqual(found, {"Memory": "KNOWLEDGE", "Evidence": "EVIDENCE",
                                 "Reasoning": "PROPOSAL", "Validation": "EVIDENCE",
                                 "Trace": "TRACE", "Decision": "DECISION"})

    def test_trust_levels_are_text_so_the_boundary_is_never_colour_alone(self):
        for level in ("KNOWLEDGE", "EVIDENCE", "OBSERVATION", "PROPOSAL", "DECISION", "TRACE"):
            with self.subTest(level=level):
                self.assertRegex(SCRIPT, rf"\b{level}\b",
                                 f"{level} appears nowhere in the console as a string")
        self.assertIn("::selection", CONSOLE_HTML, "selection must be styled, not suppressed")

    def test_the_case_header_shows_the_statement_and_marks_unstated_fields(self):
        self.assertEqual(_run(self.ctx, "document.getElementById('case-title').textContent"),
                         "billing-service retry duplicate invoice")
        chips = {chip["text"]: chip["cls"] for chip in _data(self.ctx, """
          document.getElementById("chips").querySelectorAll(".chip").map(function (c) {
            return { cls: c.className, text: c.textContent };
          })
        """)}
        self.assertIn("servicebilling-service", chips)
        self.assertIn("regioneu-west-1", chips)
        self.assertIn("unset", chips["proxynot stated"])
        self.assertIn("unset", chips["runtimenot stated"])
        self.assertNotIn("unset", chips["servicebilling-service"])

    def test_evidence_rows_show_provenance_and_a_copy_action(self):
        rows = _data(self.ctx, """
          (function () {
            var panels = document.body.querySelectorAll(".panel");
            for (var i = 0; i < panels.length; i++) {
              if (panels[i].dataset.panel !== "Evidence") continue;
              return panels[i].querySelectorAll(".row").map(function (r) {
                var src = r.querySelector(".src");
                var button = r.querySelector("button");
                return { text: r.textContent,
                         source: src ? src.textContent : null,
                         copy: button ? button.getAttribute("aria-label") : null };
              });
            }
            return [];
          })()
        """)
        self.assertEqual(len(rows), 2)
        self.assertIn("billing-service", rows[0]["text"])
        self.assertIn("engineer", rows[0]["source"])
        self.assertEqual(rows[0]["copy"], "Copy evidence service")
        self.assertIn("not stated", rows[1]["text"])

    def test_recalled_cases_render_as_cards_with_their_reasons(self):
        case = _data(self.ctx, """
          (function () {
            var panels = document.body.querySelectorAll(".panel");
            for (var i = 0; i < panels.length; i++) {
              if (panels[i].dataset.panel !== "Memory") continue;
              var card = panels[i].querySelector(".card");
              return { ref: card.querySelector(".cid").textContent,
                       text: card.textContent,
                       aria: card.querySelector("button").getAttribute("aria-label") };
            }
            return null;
          })()
        """)
        self.assertEqual(case["ref"], "seed-001")
        self.assertIn("same duplicate invoice signature", case["text"])
        self.assertIn("outcome: resolved", case["text"])
        self.assertEqual(case["aria"], "Copy recalled case seed-001")

    def test_validation_groups_the_status_rather_than_merging_it(self):
        groups = _data(self.ctx, """
          (function () {
            var panels = document.body.querySelectorAll(".panel");
            for (var i = 0; i < panels.length; i++) {
              if (panels[i].dataset.panel !== "Validation") continue;
              return panels[i].querySelectorAll(".card").map(function (card) {
                return { head: card.querySelector(".ctext").textContent,
                         text: card.textContent };
              });
            }
            return [];
          })()
        """)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["head"], "Insufficient evidence")
        self.assertIn("environment in conflict", groups[0]["text"])
        self.assertIn("engineer decision: accept", groups[0]["text"])

    def test_the_trace_is_collapsible_and_does_not_dominate(self):
        details = _data(self.ctx, """
          (function () {
            var panels = document.body.querySelectorAll(".panel");
            for (var i = 0; i < panels.length; i++)
              if (panels[i].dataset.panel === "Trace") {
                return { tag: panels[i].tagName, open: panels[i].open,
                         lines: panels[i].querySelectorAll(".events li").length };
              }
            return null;
          })()
        """)
        self.assertEqual(details["tag"], "DETAILS")
        self.assertFalse(details["open"], "the trace starts open and crowds out the content")
        self.assertEqual(details["lines"], 2)

    # -- the decision -------------------------------------------------------

    def test_a_card_does_not_repeat_a_value_its_own_detail_already_carries(self):
        """"H1 generic" beside the identifier reads as part of it, and then repeats two lines down."""
        heads = _data(self.ctx, """
          (function () {
            var panels = document.body.querySelectorAll(".panel");
            for (var i = 0; i < panels.length; i++) {
              if (panels[i].dataset.panel !== "Reasoning") continue;
              return panels[i].querySelectorAll(".card").map(function (card) {
                return { head: card.querySelector(".chead").textContent,
                         detail: card.querySelector(".cmeta").textContent };
              });
            }
            return [];
          })()
        """)
        self.assertEqual([card["head"] for card in heads],
                         ["H1Copy", "H2Copy", "H3Copy"])
        for card in heads:
            with self.subTest(head=card["head"]):
                self.assertTrue(card["detail"].startswith("relevance: "),
                                f"the relevance should be stated once, in the detail: {card['detail']!r}")
                self.assertNotIn("relevance: ", card["head"])

    def test_the_decision_actions_are_present_and_wired(self):
        _state_claim(self.ctx)
        actions = _data(self.ctx, """
          (function () {
            var box = document.getElementById("decision");
            return {
              state: box.dataset.state,
              question: box.dataset.question,
              trust: box.querySelector(".trust").textContent,
              buttons: box.querySelectorAll("button")
                .filter(function (b) { return !!b.dataset.decision; })
                .map(function (b) {
                  return { decision: b.dataset.decision,
                           label: b.getAttribute("aria-label"), text: b.textContent,
                           cls: b.className };
                })
            };
          })()
        """)
        self.assertEqual(actions["state"], "awaiting")
        self.assertEqual(actions["question"], "decide:H1")
        self.assertEqual(actions["trust"], "DECISION")
        self.assertEqual([b["decision"] for b in actions["buttons"]],
                         ["accept", "modify", "reject"])
        self.assertEqual([b["text"] for b in actions["buttons"]], ["Accept", "Modify", "Reject"])
        # Accept is the primary action and Reject is styled as the destructive one.
        self.assertIn("primary", actions["buttons"][0]["cls"])
        self.assertIn("danger", actions["buttons"][2]["cls"])
        for button in actions["buttons"]:
            self.assertIn("H1", button["label"], "an action must name the hypothesis it applies to")

    def test_accept_is_unavailable_until_the_claim_is_stated(self):
        """`verify()` raises without a claim, so the page must not offer the actions yet."""
        self.assertIn("Choose whether the evidence supports",
                      _run(self.ctx, "document.getElementById('decision').textContent"))
        self.assertEqual(_decisions(self.ctx), [], "an action was offered before the claim was stated")
        _state_claim(self.ctx)
        self.assertNotIn("Choose whether the evidence supports",
                         _run(self.ctx, "document.getElementById('decision').textContent"))
        self.assertEqual(_decisions(self.ctx), ["accept", "modify", "reject"])

    def test_a_missing_evidence_gap_does_not_demand_a_claim(self):
        payload = json.loads(json.dumps(SNAPSHOT))
        payload["pending"]["missing"] = ["proxy"]
        _load(self.ctx, payload)
        text = _run(self.ctx, "document.getElementById('decision').textContent")
        self.assertIn("current evidence lacks", text)
        self.assertIn("insufficient_evidence however strong the memory match", text)
        self.assertNotIn("Choose whether the evidence supports", text)
        self.assertEqual(_decisions(self.ctx), ["accept", "modify", "reject"])

    def test_reject_requires_a_second_deliberate_press(self):
        _state_claim(self.ctx)
        self.assertEqual(_decisions(self.ctx), ["accept", "modify", "reject"])
        # One press must only ask. It must not answer.
        _run(self.ctx, """
          (function () {
            var buttons = document.getElementById("decision").querySelectorAll("button");
            for (var i = 0; i < buttons.length; i++)
              if (buttons[i].dataset.decision === "reject") buttons[i].click();
          })()
        """)
        panel = _run(self.ctx, "document.getElementById('decision').textContent")
        self.assertIn("Yes, reject it", panel)
        self.assertIn("recorded as rejected", panel)
        self.assertEqual(_copied(self.ctx), [], "asking to reject must not copy anything")
        self.assertEqual(_data(self.ctx, "String(__log.posts ? __log.posts.length : 0)"), "0",
                         "asking to reject must not post an answer")

    def test_the_answer_carries_the_token_of_the_question_it_was_written_for(self):
        posted = _data(self.ctx, """
          (function () {
            __log.posts = [];
            fetch = function (url, options) {
              __log.posts.push({ url: url, body: JSON.parse(options.body) });
              return Promise.resolve({ ok: true, status: 200,
                                      json: function () { return Promise.resolve({}); } });
            };
            var buttons = document.getElementById("decision").querySelectorAll("button");
            for (var i = 0; i < buttons.length; i++)
              if (buttons[i].textContent === "Supports it") buttons[i].click();
            var after = document.getElementById("decision").querySelectorAll("button");
            for (var j = 0; j < after.length; j++)
              if (after[j].dataset.decision === "accept") after[j].click();
            return __log.posts;
          })()
        """)
        _drain(self.ctx)
        self.assertEqual(len(posted), 1, posted)
        self.assertTrue(posted[0]["url"].endswith("/decision"))
        self.assertEqual(posted[0]["body"]["question"], "decide:H1")
        self.assertEqual(posted[0]["body"]["decision"], "accept")
        self.assertEqual(posted[0]["body"]["claim"], "supported")

    def test_the_claim_choices_are_mutually_exclusive_and_do_not_deselect(self):
        """A second click on the chosen reading must not clear it.

        As a toggle it would: Accept would silently disappear, and an engineer who mis-clicked once
        would see the action they were about to press vanish with no explanation.
        """
        _state_claim(self.ctx, "Supports it")
        self.assertEqual(_decisions(self.ctx), ["accept", "modify", "reject"])
        _state_claim(self.ctx, "Supports it")
        self.assertEqual(_decisions(self.ctx), ["accept", "modify", "reject"],
                         "a second click on the selected reading cleared the claim")
        # And switching to the other reading works.
        _state_claim(self.ctx, "Contradicts it")
        posted = _data(self.ctx, """
          (function () {
            __log.posts = [];
            fetch = function (url, options) {
              __log.posts.push({ url: url, body: JSON.parse(options.body) });
              return Promise.resolve({ ok: true, status: 200,
                                      json: function () { return Promise.resolve({}); } });
            };
            var buttons = document.getElementById("decision").querySelectorAll("button");
            for (var i = 0; i < buttons.length; i++)
              if (buttons[i].dataset.decision === "accept") buttons[i].click();
            return __log.posts;
          })()
        """)
        _drain(self.ctx)
        self.assertEqual(posted[0]["body"]["claim"], "contradicted")
        pressed = _data(self.ctx, """
          (function () {
            return Array.from(document.getElementById("decision").querySelectorAll(".seg button"))
              .map(function (b) { return b.textContent + "=" + b.getAttribute("aria-pressed"); });
          })()
        """)
        self.assertIn("Contradicts it=true", pressed)
        self.assertIn("Supports it=false", pressed)

    def test_contradicting_is_offered_alongside_supporting(self):
        """Both readings are legal; the page must not presume one."""
        _state_claim(self.ctx, "Contradicts it")
        posted = _data(self.ctx, """
          (function () {
            __log.posts = [];
            fetch = function (url, options) {
              __log.posts.push({ url: url, body: JSON.parse(options.body) });
              return Promise.resolve({ ok: true, status: 200,
                                      json: function () { return Promise.resolve({}); } });
            };
            var buttons = document.getElementById("decision").querySelectorAll("button");
            for (var i = 0; i < buttons.length; i++)
              if (buttons[i].dataset.decision === "accept") buttons[i].click();
            return __log.posts;
          })()
        """)
        _drain(self.ctx)
        self.assertEqual(posted[0]["body"]["claim"], "contradicted")

    def test_the_page_answers_the_question_that_is_open_not_one_it_remembered(self):
        """If the open question moves to H2, the page sends H2's token - never H1's."""
        payload = json.loads(json.dumps(SNAPSHOT))
        payload["pending"]["token"] = "decide:H2"
        payload["pending"]["hypothesis_ref"] = "H2"
        payload["pending"]["statement"] = H2
        _load(self.ctx, payload)
        self.assertEqual(_run(self.ctx, "document.getElementById('decision').dataset.question"),
                         "decide:H2")
        self.assertIn("H2", _run(self.ctx, "document.getElementById('decision').textContent"))

    def test_the_resolution_form_asks_for_what_build_resolution_requires(self):
        payload = json.loads(json.dumps(SNAPSHOT))
        payload["pending"] = {"kind": "resolve", "session_id": "s1", "token": "resolve:",
                              "mismatched": [], "missing": [], "options": []}
        _load(self.ctx, payload)
        labels = _data(self.ctx, """
          document.getElementById("decision").querySelectorAll("label").map(function (l) {
            return l.textContent;
          })
        """)
        self.assertIn("What did you do? *", labels)
        self.assertIn("What did you observe afterwards? *", labels)
        self.assertIn("Confirmed root cause (leave blank if not confirmed)", labels)
        self.assertIn("Outcome", labels)
        self.assertNotIn("prompt(", SCRIPT, "the console must not use a blocking browser dialog")

    # -- timeline, boundaries, accessibility --------------------------------

    def test_the_timeline_reports_a_stage_that_did_not_run_as_not_run(self):
        stages = {stage["name"]: stage for stage in _data(self.ctx, """
          document.getElementById("timeline").querySelectorAll(".stage").map(function (s) {
            return { name: s.childNodes[1].textContent, why: s.childNodes[2].textContent,
                     cls: s.className };
          })
        """)}
        self.assertEqual(set(stages), {"Memory", "Evidence", "Workers", "Reasoning",
                                       "Validation", "Decision"})
        self.assertIn("done", stages["Memory"]["cls"])
        self.assertIn("skipped", stages["Workers"]["cls"])
        self.assertEqual(stages["Workers"]["why"], "did not run")
        self.assertNotIn("done", stages["Workers"]["cls"])

    def test_the_stage_awaiting_a_decision_is_not_also_reported_as_not_run(self):
        """A stage in progress and a stage that did not run are different claims about the run.

        The current stage has no panel yet - `investigate()` has not recorded one - so a naive render
        labelled it "did not run" while highlighting it as current, which is both true and its opposite.
        """
        stages = {stage["name"]: stage for stage in _data(self.ctx, """
          document.getElementById("timeline").querySelectorAll(".stage").map(function (s) {
            return { name: s.childNodes[1].textContent, why: s.childNodes[2].textContent,
                     cls: s.className };
          })
        """)}
        self.assertIn("current", stages["Validation"]["cls"])
        self.assertEqual(stages["Validation"]["why"], "awaiting you")
        self.assertNotEqual(stages["Validation"]["why"], "did not run")
        # Decision has not been reached at all, so it may honestly say so.
        self.assertEqual(stages["Decision"]["why"], "did not run")
        self.assertIn("skipped", stages["Decision"]["cls"])

    def test_a_running_stage_reads_as_in_progress_not_as_awaiting(self):
        payload = json.loads(json.dumps(SNAPSHOT))
        payload["pending"] = None
        _load(self.ctx, payload)
        stages = {stage["name"]: stage for stage in _data(self.ctx, """
          document.getElementById("timeline").querySelectorAll(".stage").map(function (s) {
            return { name: s.childNodes[1].textContent, why: s.childNodes[2].textContent,
                     cls: s.className };
          })
        """)}
        # The last STAGE that reported, which is not the trace - the trace is not a stage.
        self.assertEqual(stages["Validation"]["why"], "in progress")
        self.assertIn("current", stages["Validation"]["cls"])
        self.assertNotEqual(stages["Validation"]["why"], "awaiting you")

    def test_no_domain_specific_text_is_ever_rendered(self):
        """Word boundaries, not substrings: "contradicts" is not a mention of CTS."""
        text = _run(self.ctx, "document.body.textContent")
        for term in ("vlsi", "sdc", "sta", "opensta", "primetime", "signoff", "cts",
                     "synthesis", "placement", "routing", "clock tree", "constraint"):
            with self.subTest(term=term):
                self.assertIsNone(re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE),
                                  f"the console invented domain content: {term!r}")

    def test_text_selection_is_never_disabled(self):
        """Copying is offered; selection must stay the engineer's to use."""
        style = CONSOLE_HTML.split("<style>")[1].split("</style>")[0]
        for hostile in ("user-select: none", "user-select:none", "-webkit-user-select: none",
                        "oncontextmenu", "ondragstart"):
            self.assertNotIn(hostile, style)

    def test_every_button_is_a_real_button_with_an_accessible_name(self):
        buttons = _data(self.ctx, """
          document.body.querySelectorAll("button").map(function (b) {
            return { type: b.getAttribute("type"), label: b.getAttribute("aria-label"),
                     text: b.textContent };
          })
        """)
        self.assertTrue(buttons)
        for button in buttons:
            with self.subTest(label=button["label"]):
                self.assertEqual(button["type"], "button", "a button defaulted to type=submit")
                self.assertTrue(button["label"] or button["text"].strip(),
                                "a button with neither an aria-label nor text")

    def test_a_visible_focus_state_is_defined(self):
        self.assertIn(":focus-visible", CONSOLE_HTML)
        self.assertIn("outline", CONSOLE_HTML)

    def test_the_layout_collapses_on_a_narrow_viewport(self):
        style = CONSOLE_HTML.split("<style>")[1].split("</style>")[0]
        self.assertIn("grid-template-columns: minmax(0, 1fr) 384px", style)
        narrow = style.split("@media (max-width: 1180px)")[1].split("@media")[0]
        self.assertIn(".workspace { display: flex; flex-direction: column; }", narrow)
        self.assertIn("overflow-x: hidden", style, "the page must not scroll sideways")

    def test_the_decision_comes_first_when_the_columns_collapse(self):
        """Once the rail is no longer beside the content, the decision must not end up below the fold.

        The engineer has read the evidence; the thing waiting on them is the decision. Ordering it last
        in a single column would put a prompt to act below pages of context they do not need again.
        """
        narrow = CONSOLE_HTML.split("@media (max-width: 1180px)")[1].split("@media")[0]
        self.assertIn(".decide { order: -1; }", narrow)
        self.assertIn(".col { display: contents; }", narrow,
                      "the wrappers must stop being boxes for the panels to be ordered")
        self.assertIn(".panel.trace { order: 10; }", narrow,
                      "the trace is process, and goes last")

    def test_a_poll_that_changes_nothing_does_not_rebuild_the_dom(self):
        """Re-rendering on every poll destroyed selection, focus, scroll and the Copied state.

        A marker is written onto a live node, an identical snapshot is rendered, and the marker is read
        back: if the DOM had been rebuilt the node would be a different object with no marker.
        """
        _click_hypothesis(self.ctx, "H2")
        self.assertEqual(_data(self.ctx, """
          (function () {
            var panels = document.body.querySelectorAll(".panel");
            for (var i = 0; i < panels.length; i++) {
              if (panels[i].dataset.panel !== "Reasoning") continue;
              panels[i].querySelectorAll(".card")[0].querySelector("button").dataset.marker = "kept";
            }
            return true;
          })()
        """), True)
        _poll(self.ctx, SNAPSHOT)
        marker = _data(self.ctx, """
          (function () {
            var panels = document.body.querySelectorAll(".panel");
            for (var i = 0; i < panels.length; i++) {
              if (panels[i].dataset.panel !== "Reasoning") continue;
              var button = panels[i].querySelectorAll(".card")[0].querySelector("button");
              return button.dataset.marker || null;
            }
            return null;
          })()
        """)
        self.assertEqual(marker, "kept", "the DOM was rebuilt for an unchanged snapshot")
        # And the Copied state survived the poll, which is the user-visible consequence.
        buttons = {b["key"]: b for b in _copy_buttons(self.ctx) if b["key"].startswith("hyp:")}
        self.assertEqual(buttons["hyp:H2"]["word"], "Copied")

if __name__ == "__main__":
    unittest.main()
