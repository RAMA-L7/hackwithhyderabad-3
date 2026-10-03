"""The console page. One self-contained document, served inline, standard library only.

# What this file is

`web_server.py` moves bytes; this constant is the whole client. It renders the `SessionViewModel` the
server already sends, and posts the engineer's answers back through the endpoints above. It contains no
investigation logic: it cannot decide whether a hypothesis is right, cannot derive a fact, and cannot
conclude anything. Every judgement on the page is the engineer's, and every conclusion the page shows was
reached in `investigate()` and arrived here as data.

# The defects this rewrite fixes

**1. The page could not complete a session.** The old decision handler sent
`{decision, note, confirmed}` and never sent `claim`. `verify()` requires the engineer to state whether
current evidence supports or contradicts a hypothesis whenever nothing is missing, so accepting a
hypothesis with complete evidence raised `VerificationError` and killed the run. The claim choice is now
part of the decision form and is required exactly when the question reports no missing evidence - which
is precisely when the backend needs it. The page asks because the server told it to, not because it
guessed.

**2. No hypothesis could be copied.** There was no per-item identity to copy, address or name. The view
model now carries `ref` (`H1`, a case id) separately from `label` (the text), which is what makes
`aria-label="Copy hypothesis H2"` able to mean anything. Each copy button closes over its own item's
`label` in `makeCopyButton(text, key, ariaLabel)`. There is no delegated handler and no shared mutable
target, so H1, H2 and H3 cannot read each other's text.

**3. Polling destroyed the page every 700ms.** The old render cleared and rebuilt the DOM on every poll,
which destroyed text selection, focus, scroll position and any transient button state - so "Copied" could
never survive long enough to be read, and selecting a hypothesis to copy it by hand was impossible. The
console now compares the incoming snapshot against the last rendered one and re-renders only on a real
change. Transient state (`copied`, `form`, `confirm`) lives in a JS object, never in a node a poll
rebuilds.

# Layout

    top bar      product | session | status | connection
    case header  the problem statement, then environment chips
    timeline     which stages ran, which is current, which did not run
    workspace    left: memory, evidence, workers, reasoning, validation
                 right: the engineer's decision, then the collapsible trace

The right column is sticky because the engineer's pending decision must stay reachable while they read
the reasoning it applies to. Trace lives there and collapses, because it is process rather than content.

# Trust labels

Every panel declares which authority produced its contents, and the label is always text as well as
colour: `KNOWLEDGE`, `EVIDENCE`, `OBSERVATION`, `PROPOSAL`, `DECISION`, `TRACE`. A panel that did not run
is not rendered at all - absent, empty, and "found nothing" are three different states and the console
keeps them apart.

# Clipboard

`navigator.clipboard` is used when available. It is gated on a secure context, and plain-HTTP loopback is
not treated as one everywhere, so there is a `textarea` + `execCommand` fallback that copies the same
string and nothing else. Neither path copies HTML, and neither runs unless a copy button is pressed, so
text selection is never disabled anywhere on the page.
"""

from __future__ import annotations

CONSOLE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Engineering Debugger</title>
<style>
  :root {
    --bg: #0d1015; --surface: #14181f; --surface-2: #1a1f28;
    --line: #262c37; --line-soft: #1e232c;
    --text: #e6eaf2; --text-dim: #a3adbf; --text-faint: #7f8999;
    --accent: #6ea8fe; --focus: #8ab4ff;
    --know: #7cb8f5; --evid: #63c79b; --obs: #e0ac5a; --prop: #b490f0;
    --dec: #f08a8a; --trace: #8f9aad;
    --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
    --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  * { box-sizing: border-box; }
  html, body { max-width: 100%; overflow-x: hidden; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.5 var(--sans);
         -webkit-font-smoothing: antialiased; }
  /* Selection is deliberately left alone everywhere: copying is offered, never enforced. */
  ::selection { background: #2b4a7a; color: #fff; }
  :where(button, input, select, textarea, summary, [tabindex]):focus-visible {
    outline: 2px solid var(--focus); outline-offset: 2px; border-radius: 3px;
  }
  .sr { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
        overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }

  .topbar { position: sticky; top: 0; z-index: 30; display: flex; align-items: center; gap: 20px;
            padding: 0 20px; height: 48px; background: var(--surface);
            border-bottom: 1px solid var(--line); }
  .brand { font-weight: 600; white-space: nowrap; }
  .brand span { color: var(--text-faint); font-weight: 400; }
  .topmeta { display: flex; align-items: center; gap: 18px; margin-left: auto;
             font: 12px/1 var(--mono); color: var(--text-dim); }
  .topmeta b { color: var(--text); font-weight: 500; }
  .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px;
         vertical-align: 1px; background: var(--text-faint); }
  .dot.ok { background: var(--evid); } .dot.wait { background: var(--obs); }
  .dot.bad { background: var(--dec); } .dot.idle { background: var(--text-faint); }

  .case { padding: 18px 20px 14px; border-bottom: 1px solid var(--line); }
  .case h1 { margin: 0 0 10px; font-size: 19px; font-weight: 600; line-height: 1.35;
             overflow-wrap: anywhere; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { display: inline-flex; align-items: baseline; gap: 6px; padding: 3px 8px;
          background: var(--surface-2); border: 1px solid var(--line); border-radius: 3px;
          font: 12px/1.4 var(--mono); }
  .chip b { color: var(--text-faint); font-weight: 400; }
  .chip .v { color: var(--text); overflow-wrap: anywhere; }
  .chip.unset .v { color: var(--text-faint); font-style: italic; }
  .symptoms { margin: 10px 0 0; padding: 0; list-style: none; display: flex;
              flex-direction: column; gap: 3px; }
  .symptoms li { color: var(--text-dim); font-size: 13px; padding-left: 12px;
                 border-left: 2px solid var(--line); overflow-wrap: anywhere; }

  .timeline { display: flex; flex-wrap: wrap; padding: 0 20px; background: var(--surface);
              border-bottom: 1px solid var(--line); }
  .stage { display: flex; align-items: center; gap: 7px; padding: 11px 16px 11px 0; margin-right: 16px;
           font-size: 12px; color: var(--text-faint); border-bottom: 2px solid transparent; }
  .stage .mark { width: 8px; height: 8px; border-radius: 50%; flex: none;
                 border: 1px solid var(--text-faint); }
  .stage.done { color: var(--text-dim); }
  .stage.done .mark { background: var(--evid); border-color: var(--evid); }
  .stage.current { color: var(--text); border-bottom-color: var(--obs); font-weight: 500; }
  .stage.current .mark { background: var(--obs); border-color: var(--obs);
                         box-shadow: 0 0 0 3px rgba(224,172,90,.16); }
  .stage.skipped .mark { border-style: dashed; }
  .stage .why { color: var(--text-faint); font-size: 11px; }

  .workspace { display: grid; grid-template-columns: minmax(0, 1fr) 384px; gap: 18px;
               padding: 18px 20px 64px; align-items: start; }
  .col { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
  .rail { position: sticky; top: 66px; }

  .panel { background: var(--surface); border: 1px solid var(--line); border-radius: 4px;
           min-width: 0; }
  .panel > header, details.trace > summary { display: flex; align-items: center; gap: 10px;
                                             padding: 9px 12px; }
  .panel > header { border-bottom: 1px solid var(--line-soft); }
  .panel > header h2, summary h2 { margin: 0; font-size: 12px; font-weight: 600; letter-spacing: .06em;
                                  text-transform: uppercase; color: var(--text-dim); }
  .panel .count { margin-left: auto; font: 11px/1 var(--mono); color: var(--text-faint); }
  .panel .note { padding: 8px 12px; border-top: 1px solid var(--line-soft); color: var(--text-faint);
                 font-size: 12px; overflow-wrap: anywhere; }
  .body { padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }

  /* The trust label is always text; colour reinforces it and never carries it alone. */
  .trust { font: 10px/1 var(--mono); letter-spacing: .07em; padding: 3px 6px;
           border: 1px solid currentColor; border-radius: 2px; white-space: nowrap; }
  .t-KNOWLEDGE { color: var(--know); } .t-EVIDENCE { color: var(--evid); }
  .t-OBSERVATION { color: var(--obs); } .t-PROPOSAL { color: var(--prop); }
  .t-DECISION { color: var(--dec); } .t-TRACE { color: var(--trace); }

  .row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; min-width: 0; }
  .row .k { font: 12px/1.5 var(--mono); color: var(--text-dim); overflow-wrap: anywhere; }
  .row .v { font: 13px/1.5 var(--mono); color: var(--text); overflow-wrap: anywhere; }
  .row .cid { font: 12px/1.5 var(--mono); color: var(--text-dim); letter-spacing: .04em; }
  .row .meta { flex-basis: 100%; color: var(--text-faint); font-size: 12px; overflow-wrap: anywhere; }
  .src { font: 11px/1.5 var(--mono); color: var(--text-faint); overflow-wrap: anywhere; }
  .src::before { content: "source "; color: var(--trace); }

  .card { border: 1px solid var(--line); border-left: 2px solid var(--line); border-radius: 3px;
          padding: 10px 12px; background: var(--surface-2); min-width: 0; }
  .card + .card { margin-top: 8px; }
  .card .chead { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .card .cid { font: 12px/1 var(--mono); color: var(--text-dim); letter-spacing: .04em;
               white-space: nowrap; }
  .card .ctext { color: var(--text); font-size: 14px; line-height: 1.55; overflow-wrap: anywhere; }
  .card .cmeta { margin-top: 7px; color: var(--text-faint); font-size: 12px; overflow-wrap: anywhere; }
  .card.proposal { border-left-color: var(--prop); } .card.knowledge { border-left-color: var(--know); }
  .card.observation { border-left-color: var(--obs); } .card.evidence { border-left-color: var(--evid); }
  .card.decision { border-left-color: var(--dec); }

  button { font: inherit; color: var(--text); background: var(--surface-2); border: 1px solid var(--line);
           border-radius: 3px; padding: 6px 11px; cursor: pointer; }
  button:hover:not(:disabled) { background: #222836; border-color: #38404f; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  .copy { margin-left: auto; padding: 3px 8px; font: 11px/1.4 var(--mono); color: var(--text-dim);
          display: inline-flex; align-items: center; gap: 5px; }
  .copy .flag { width: 6px; height: 6px; border-radius: 50%; background: transparent; flex: none; }
  .copy[data-copied="1"] { color: var(--evid); border-color: var(--evid); }
  .copy[data-copied="1"] .flag { background: var(--evid); }

  .decide { border-color: #5c4a20; }
  .decide > header { background: #1d1a11; border-bottom-color: #3a3220; }
  .decide > header h2 { color: var(--obs); }
  .decide.idle { border-color: var(--line); }
  .decide.idle > header { background: var(--surface-2); border-bottom-color: var(--line-soft); }
  .decide.idle > header h2 { color: var(--text-dim); }
  .qcard { border: 1px solid #4a3d1c; border-left: 2px solid var(--obs); border-radius: 3px;
           padding: 10px 12px; background: #191710; }
  .qcard .qid { font: 12px/1 var(--mono); color: var(--obs); letter-spacing: .05em; }
  .qcard .qtext { margin-top: 6px; font-size: 14px; line-height: 1.55; overflow-wrap: anywhere; }
  .gaps { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; }
  .gap { font: 12px/1.5 var(--mono); overflow-wrap: anywhere; }
  .gap .gk { color: var(--text-dim); } .gap .gv { color: var(--obs); }
  .gap.blocked .gv { color: var(--dec); }
  .why { margin-top: 8px; font-size: 12px; color: var(--text-dim); overflow-wrap: anywhere; }
  fieldset { border: 0; margin: 0; padding: 0; }
  legend { padding: 0; font-size: 12px; color: var(--text-dim); }
  .need { color: var(--obs); }
  .seg { display: flex; gap: 6px; margin-top: 5px; flex-wrap: wrap; }
  .seg button { flex: 1 1 auto; min-width: 110px; }
  .seg button[aria-pressed="true"] { border-color: var(--accent); color: var(--accent);
                                     background: #16233a; }
  .inp { width: 100%; margin-top: 5px; padding: 7px 9px; background: #0b0e13; color: var(--text);
         border: 1px solid var(--line); border-radius: 3px; font: 13px/1.5 var(--mono); }
  textarea.inp { resize: vertical; min-height: 56px; }
  .field { margin-top: 11px; }
  .actions { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
  .primary { background: #1d3a5f; border-color: #2f5a8f; color: #dce9fb; font-weight: 600; }
  .primary:hover:not(:disabled) { background: #24487a; border-color: #3a6cb0; }
  .danger { color: var(--dec); border-color: #4a2b2b; }
  .danger:hover:not(:disabled) { background: #2a1a1a; border-color: #6b3a3a; }
  .confirm { flex-basis: 100%; margin-top: 4px; padding: 10px 12px; border: 1px solid #4a2b2b;
             border-radius: 3px; background: #1d1414; }
  .confirm p { margin: 0 0 8px; font-size: 12px; color: var(--text-dim); }

  details.trace { background: var(--surface); }
  details.trace > summary { list-style: none; cursor: pointer; }
  details.trace > summary::-webkit-details-marker { display: none; }
  details.trace > summary::after { content: "\\25be"; margin-left: auto; color: var(--text-faint);
                                   font-size: 10px; }
  details.trace[open] > summary::after { content: "\\25b4"; }
  .events { list-style: none; margin: 0; padding: 4px 12px 10px; border-top: 1px solid var(--line-soft); }
  .events li { display: flex; gap: 10px; padding: 3px 0; font: 12px/1.5 var(--mono);
               color: var(--text-dim); }
  .events .t { color: var(--trace); flex: none; }
  .events .e { overflow-wrap: anywhere; }

  .empty { color: var(--text-faint); font-size: 13px; }
  .banner { margin: 0 20px 14px; padding: 10px 12px; border: 1px solid #4a2b2b; border-radius: 3px;
            background: #1d1414; color: #f2b8b8; font-size: 13px; overflow-wrap: anywhere; }
  .start { padding: 18px 20px; border-bottom: 1px solid var(--line); }
  .start textarea { width: 100%; min-height: 76px; padding: 9px 11px; background: #0b0e13;
                     color: var(--text); border: 1px solid var(--line); border-radius: 3px;
                     font: 13px/1.5 var(--mono); resize: vertical; }
  .start .row2 { display: flex; align-items: center; gap: 12px; margin-top: 9px; }
  .hint { color: var(--text-faint); font-size: 12px; }
  code { font: 12px/1.5 var(--mono); color: var(--text-dim); background: var(--surface-2);
         padding: 1px 4px; border-radius: 2px; }

  @media (max-width: 1180px) {
    /* One column. The two wrappers stop being boxes so their panels become siblings of the grid, which
       is what lets the DECISION be ordered to the top: once the rail is no longer beside the content,
       leaving the decision at the end of the document would put the one thing that is waiting on the
       engineer below the fold, behind pages of evidence they have already read. The trace goes last,
       because it is process rather than content. */
    .workspace { display: flex; flex-direction: column; }
    .col { display: contents; }
    .decide { order: -1; }
    .panel.trace { order: 10; }
  }
  @media (max-width: 720px) {
    .topbar { height: auto; flex-wrap: wrap; padding: 8px 14px; gap: 10px; }
    .topmeta { margin-left: 0; gap: 12px; }
    .case, .timeline, .workspace, .start { padding-left: 14px; padding-right: 14px; }
    .banner { margin-left: 14px; margin-right: 14px; }
  }
  @media (prefers-reduced-motion: no-preference) {
    .stage.current .mark { animation: pulse 2.4s ease-in-out infinite; }
    @keyframes pulse { 0%, 100% { opacity: 1 } 50% { opacity: .45 } }
  }
</style>
</head>
<body>

<header class="topbar">
  <div class="brand">Engineering Debugger <span>/ investigation console</span></div>
  <div class="topmeta">
    <span>session <b id="m-session">-</b></span>
    <span id="m-status"><i class="dot idle"></i>No session</span>
    <span id="m-conn"><i class="dot idle"></i>Idle</span>
  </div>
</header>

<section class="start" id="start">
  <label class="sr" for="description">Describe the engineering issue</label>
  <textarea id="description" spellcheck="false"
    placeholder="checkout service fails: connection pool exhausted&#10;service=checkout&#10;region=eu-west-1&#10;pool exhausted in the logs after 5 retries"></textarea>
  <div class="row2">
    <button type="button" id="start-btn">Start investigation</button>
    <span class="hint">First line is the summary. <code>key=value</code> states an environment fact;
      other lines are symptoms. Keys: service, runtime, proxy, region.</span>
  </div>
</section>

<div id="banner" hidden></div>
<div class="case" id="case" hidden>
  <h1 id="case-title"></h1>
  <div class="chips" id="chips"></div>
  <ul class="symptoms" id="symptoms"></ul>
</div>
<nav class="timeline" id="timeline" aria-label="Investigation progress"></nav>

<main class="workspace">
  <div class="col" id="left"></div>
  <div class="col rail" id="right"></div>
</main>

<div class="sr" id="live" role="status" aria-live="polite"></div>
<div class="sr" id="copy-live" role="status" aria-live="polite"></div>

<script>
"use strict";

/* The six stages an investigation passes through. A stage the record does not contain is reported as
   not-run and never drawn as completed: an absent Workers panel means no worker stage ran, and showing
   it as done would be a false claim about work that never happened. */
const STAGES = [
  ["Memory", "Knowledge recalled"],
  ["Evidence", "Current facts"],
  ["Workers", "Deterministic workers"],
  ["Reasoning", "Agent proposals"],
  ["Validation", "Checked against evidence"],
  ["Decision", "Engineer judgement"]
];
const TRUST_CLASS = {
  KNOWLEDGE: "t-KNOWLEDGE", EVIDENCE: "t-EVIDENCE", OBSERVATION: "t-OBSERVATION",
  PROPOSAL: "t-PROPOSAL", DECISION: "t-DECISION", TRACE: "t-TRACE"
};
const OUTCOME_TEXT = { resolved: "Resolved", declined: "Declined", running: "Investigating" };
const VALIDATION_HEADS = {
  supported: "Supported by current evidence",
  contradicted: "Contradicted by current evidence",
  insufficient_evidence: "Insufficient evidence"
};

const state = {
  id: null, timer: null, busy: false, snapshot: null, rendered: "",
  /* Transient state lives here, never in a node a poll rebuilds. */
  copied: new Map(), form: {}, confirm: null
};

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function trust(level) {
  return el("span", "trust " + (TRUST_CLASS[level] || ""), level);
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
function say(message) { document.getElementById("live").textContent = message; }

/* ---------------------------------------------------------------- clipboard */

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(text); return true; } catch (err) { /* fall through */ }
  }
  const scratch = document.createElement("textarea");
  scratch.value = text;
  scratch.setAttribute("readonly", "");
  /* In the document and not display:none, or execCommand has nothing to select. */
  scratch.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0";
  document.body.appendChild(scratch);
  const selection = document.getSelection();
  const kept = selection && selection.rangeCount ? selection.getRangeAt(0) : null;
  scratch.select();
  scratch.setSelectionRange(0, text.length);
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
  document.body.removeChild(scratch);
  /* The fallback selection would otherwise eat the engineer's own selection. */
  if (kept && selection) { selection.removeAllRanges(); selection.addRange(kept); }
  return ok;
}

/* One button per item, closing over that item's text. `key` identifies the item so H1 and H2 can never
   show each other's "Copied" state, and `data-copy-text` makes the target inspectable. */
function makeCopyButton(text, key, ariaLabel) {
  const button = el("button", "copy");
  button.type = "button";
  button.setAttribute("aria-label", ariaLabel);
  button.dataset.key = key;
  button.dataset.copyText = text;
  const flag = el("span", "flag");
  const word = el("span", null, "Copy");
  button.append(flag, word);
  const paint = () => {
    const at = state.copied.get(key) || 0;
    const done = Date.now() - at < 1800;
    button.dataset.copied = done ? "1" : "0";
    word.textContent = done ? "Copied" : "Copy";
  };
  paint();
  button.addEventListener("click", async () => {
    const ok = await copyText(text);
    if (ok) {
      state.copied.set(key, Date.now());
      const spoken = ariaLabel.replace("Copy ", "Copied ");
      document.getElementById("copy-live").textContent = spoken;
      say(spoken);
    } else {
      word.textContent = "Press Ctrl+C";
      say("Copy was blocked by the browser. The text is selected; press Control C.");
    }
    paint();
    setTimeout(paint, 1900);
  });
  return button;
}

/* ---------------------------------------------------------------- top bar */

function paintTopBar(snapshot) {
  document.getElementById("m-session").textContent =
    snapshot && snapshot.session_id ? snapshot.session_id : "-";

  const status = document.getElementById("m-status");
  clear(status);
  const pending = snapshot && snapshot.pending;
  let label, tone;
  if (!snapshot) { label = "No session"; tone = "idle"; }
  else if (snapshot.status === "failed") { label = "Failed"; tone = "bad"; }
  else if (pending) { label = pending.kind === "resolve" ? "Awaiting resolution" : "Awaiting decision";
                      tone = "wait"; }
  else if (snapshot.status === "running") { label = "Investigating"; tone = "wait"; }
  else { label = OUTCOME_TEXT[snapshot.outcome] || "Complete";
         tone = snapshot.outcome === "declined" ? "idle" : "ok"; }
  status.append(el("i", "dot " + tone), document.createTextNode(label));

  const conn = document.getElementById("m-conn");
  clear(conn);
  const live = snapshot && (snapshot.status === "running" || snapshot.pending);
  conn.append(el("i", "dot " + (live ? "ok" : "idle")),
              document.createTextNode(live ? "Connected" : "Idle"));
  conn.title = "Polling the session record; the investigation itself runs on the server.";
}

function paintBanner(snapshot) {
  const banner = document.getElementById("banner");
  if (snapshot && snapshot.status === "failed" && snapshot.error) {
    banner.textContent = "Investigation failed: " + snapshot.error;
    banner.hidden = false;
  } else if (state.notice) {
    banner.textContent = state.notice;
    banner.hidden = false;
  } else {
    banner.textContent = "";
    banner.hidden = true;
  }
}

/* ---------------------------------------------------------------- case + timeline */

function paintCase(view) {
  const node = document.getElementById("case");
  if (!view) { node.hidden = true; return; }
  node.hidden = false;
  const info = view.case || null;
  document.getElementById("case-title").textContent =
    (info && info.signature) || view.title || "investigation";

  const chips = document.getElementById("chips");
  clear(chips);
  for (const fact of ((info && info.environment) || [])) {
    const chip = el("span", "chip" + (fact.stated ? "" : " unset"));
    chip.append(el("b", null, fact.name),
                el("span", "v", fact.stated ? fact.value : "not stated"));
    chip.title = fact.stated ? "Stated by the engineer" : "Not stated by the engineer";
    chips.append(chip);
  }
  const symptoms = document.getElementById("symptoms");
  clear(symptoms);
  for (const symptom of ((info && info.symptoms) || [])) symptoms.append(el("li", null, symptom));
}

function paintTimeline(view, pending) {
  const bar = document.getElementById("timeline");
  clear(bar);
  if (!view) return;
  const present = new Set((view.panels || []).map(function (panel) { return panel.name; }));
  let currentName = null;
  if (pending) currentName = pending.kind === "resolve" ? "Decision" : "Validation";
  else if (view.status === "running") {
    /* The last STAGE that has reported, not the last panel: the trace records every step and is never
       a stage, so taking the last panel would leave no stage marked current at all. */
    for (let i = STAGES.length - 1; i >= 0; i--) {
      if (present.has(STAGES[i][0])) { currentName = STAGES[i][0]; break; }
    }
  }
  for (const pair of STAGES) {
    const name = pair[0], why = pair[1];
    const ran = present.has(name);
    const isCurrent = name === currentName;
    /* Three different statements, and conflating any two of them is a false claim about the run: a
       stage in progress is not a stage that did not run, and neither is one that has already reported. */
    let label;
    if (isCurrent) label = pending ? "awaiting you" : "in progress";
    else if (ran) label = why;
    else label = "did not run";
    const node = el("div", "stage" + (isCurrent ? " current" : (ran ? " done" : " skipped")));
    node.append(el("span", "mark"), el("span", null, name), el("span", "why", label));
    node.title = isCurrent
      ? (pending ? "The investigation is blocked until the engineer answers" : "This stage is running")
      : (ran ? why + " - reported" : "No record of this stage: it did not run");
    bar.append(node);
  }
}

/* ---------------------------------------------------------------- panels */

function panelShell(name, trustLevel, count) {
  const box = el("section", "panel");
  box.dataset.panel = name;
  const head = el("header");
  head.append(el("h2", null, name), trust(trustLevel));
  if (count !== undefined) head.append(el("span", "count", String(count)));
  box.append(head);
  const body = el("div", "body");
  box.append(body);
  return { box: box, body: body };
}

function metaRow(item, panelTrust) {
  const row = el("div", "row");
  if (item.ref) row.append(el("span", "cid", item.ref));
  if (item.label) row.append(el("span", "k", item.label));
  if (item.value !== undefined && item.value !== null && item.value !== "") {
    row.append(el("span", "v", item.value));
  }
  if (item.trust && item.trust !== panelTrust) row.append(trust(item.trust));
  if (item.detail) row.append(el("span", "meta", item.detail));
  if (item.source) row.append(el("span", "src", item.source));
  return row;
}

function card(item, variant, copy) {
  const box = el("article", "card " + variant);
  const head = el("div", "chead");
  if (item.ref) head.append(el("span", "cid", item.ref));
  /* Only a value the detail does not already carry. Repeating it produced "H1 generic" beside the
     identifier, which reads as part of it, and then said the same thing again two lines down. */
  if (item.value && !(item.detail && item.detail.indexOf(item.value) >= 0)) {
    head.append(el("span", "cid", item.value));
  }
  if (item.trust) head.append(trust(item.trust));
  if (copy) head.append(makeCopyButton(copy.text, copy.key, copy.aria));
  box.append(head);
  box.append(el("div", "ctext", item.label));
  if (item.source) box.append(el("span", "src", item.source));
  if (item.detail) box.append(el("div", "cmeta", item.detail));
  return box;
}

function finish(box, body, panel, emptyText) {
  if (!panel.items.length && emptyText) body.append(el("div", "empty", emptyText));
  if (panel.note) box.append(el("div", "note", panel.note));
  return box;
}

const RENDER = {
  Memory: function (panel) {
    const shell = panelShell(panel.name, panel.trust, panel.items.length);
    for (const item of panel.items) {
      if (item.ref && item.source === "memory") {
        /* A recalled case is a card, with the recall's own reasons visible. */
        shell.body.append(card(item, "knowledge",
          { text: item.label, key: "mem:" + item.ref,
            aria: "Copy recalled case " + item.ref }));
      } else {
        shell.body.append(metaRow(item, panel.trust));
      }
    }
    return finish(shell.box, shell.body, panel, "Nothing was recalled.");
  },

  Evidence: function (panel) {
    const shell = panelShell(panel.name, panel.trust, panel.items.length);
    for (const item of panel.items) {
      const stated = item.value !== null && item.value !== undefined && item.value !== "";
      const row = el("div", "row");
      row.append(el("span", "k", item.label), el("span", "v", stated ? item.value : "not stated"));
      if (item.source) row.append(el("span", "src", item.source));
      row.append(makeCopyButton(
        item.label + ": " + (stated ? item.value : "not stated"),
        "ev:" + item.label, "Copy evidence " + item.label));
      shell.body.append(row);
    }
    return finish(shell.box, shell.body, panel, "No evidence was recorded.");
  },

  Workers: function (panel) {
    const shell = panelShell(panel.name, panel.trust, panel.items.length);
    for (const item of panel.items) {
      const variant = item.trust === "PROPOSAL" ? "proposal" : "observation";
      shell.body.append(card(item, variant,
        { text: item.label, key: "w:" + (item.ref || item.label),
          aria: "Copy worker " + (item.trust === "PROPOSAL" ? "proposal" : "observation") }));
    }
    return finish(shell.box, shell.body, panel);
  },

  Reasoning: function (panel) {
    const shell = panelShell(panel.name, panel.trust, panel.items.length);
    for (const item of panel.items) {
      /* One card per hypothesis, each with its own copy button bound to its own text. `ref` is what
         makes that possible; the button closes over `item.label` and nothing else. */
      shell.body.append(card(item, "proposal",
        { text: item.label, key: "hyp:" + (item.ref || item.label),
          aria: "Copy hypothesis " + (item.ref || "") }));
    }
    return finish(shell.box, shell.body, panel, "No hypothesis was produced.");
  },

  Validation: function (panel) {
    /* Grouped by status, so "supported", "contradicted" and "insufficient evidence" cannot be read as
       one undifferentiated block. Missing evidence is not invented here: it is not in the record, and
       the live question is the only place it is known. */
    const shell = panelShell(panel.name, panel.trust, panel.items.length);
    const byRef = new Map();
    for (const item of panel.items) {
      const key = item.ref || "?";
      if (!byRef.has(key)) byRef.set(key, []);
      byRef.get(key).push(item);
    }
    for (const entry of byRef) {
      const ref = entry[0], items = entry[1];
      const head = items.find(function (item) { return VALIDATION_HEADS[item.label]; });
      if (!head) continue;
      const group = el("div", "card evidence");
      const chead = el("div", "chead");
      chead.append(el("span", "cid", ref), el("span", "ctext", VALIDATION_HEADS[head.label]));
      group.append(chead);
      for (const item of items) {
        if (item.label === head.label) continue;
        group.append(metaRow(item, panel.trust));
      }
      if (head.value) group.append(el("div", "cmeta", "engineer decision: " + head.value));
      if (head.detail) group.append(el("div", "cmeta", head.detail));
      shell.body.append(group);
    }
    return finish(shell.box, shell.body, panel, "Nothing has been validated yet.");
  },

  Trace: function (panel) {
    const box = el("details", "panel trace");
    box.dataset.panel = panel.name;
    const summary = el("summary");
    summary.append(el("h2", null, panel.name), trust(panel.trust),
                   el("span", "count", panel.items.length + " events"));
    box.append(summary);
    const list = el("ul", "events");
    /* Process, not a claim about the design. Collapsed by default so it cannot crowd out the content
       it describes. */
    for (const item of panel.items) {
      const line = el("li");
      line.append(el("span", "t", item.ref || ""), el("span", "e", item.label));
      list.append(line);
    }
    box.append(list);
    if (panel.note) box.append(el("div", "note", panel.note));
    return box;
  }
};

function paintPanels(view) {
  const left = document.getElementById("left");
  const right = document.getElementById("right");
  clear(left);
  clear(right);
  if (!view || !(view.panels || []).length) {
    left.append(el("div", "empty", "No stage has reported yet."));
    return;
  }
  for (const panel of view.panels) {
    if (panel.name === "Trace") { right.append(RENDER.Trace(panel)); continue; }
    const render = RENDER[panel.name];
    /* The Decision panel is not rendered here: `paintDecision` owns the rail's first slot, so the live
       question and the recorded decisions are never on screen at the same time. */
    if (!render || panel.name === "Decision") continue;
    left.append(render(panel));
  }
}

/* ---------------------------------------------------------------- decision */

function formKey(pending) { return pending.kind + ":" + (pending.hypothesis_ref || ""); }

function claimToggle(form, required) {
  const seg = el("div", "seg");
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", "Does current evidence support or contradict this explanation");
  for (const pair of [["supported", "Supports it"], ["contradicted", "Contradicts it"]]) {
    const value = pair[0], text = pair[1];
    const button = el("button", null, text);
    button.type = "button";
    button.setAttribute("aria-pressed", form.claim === value ? "true" : "false");
    button.setAttribute("aria-label", "Evidence " + text.toLowerCase());
    /* Mutually exclusive, not a toggle. Two choices about the same evidence behave like radio buttons,
       and a toggle would let a second click on the selected one silently clear the claim - at which
       point Accept silently disappears and the engineer is left with a button that has gone missing. */
    button.addEventListener("click", function () {
      form.claim = value;
      paint();
    });
    seg.append(button);
  }
  if (required && !form.claim) {
    seg.append(el("div", "why need", "Choose one before deciding."));
  }
  return seg;
}

function noteField(form, key, label) {
  const wrap = el("div", "field");
  const id = "note-" + key.replace(/[^a-z0-9]/gi, "-");
  const tag = el("label", null, label);
  tag.htmlFor = id;
  const area = el("textarea", "inp");
  area.id = id;
  area.value = form.note || "";
  area.addEventListener("input", function () { form.note = area.value; });
  wrap.append(tag, area);
  return wrap;
}

function resolutionForm(form, key) {
  const wrap = el("div");
  const fields = [
    ["action_taken", "What did you do? *"],
    ["observed_result", "What did you observe afterwards? *"],
    ["root_cause_confirmed", "Confirmed root cause (leave blank if not confirmed)"]
  ];
  for (const pair of fields) {
    const name = pair[0], label = pair[1];
    const id = "res-" + name + "-" + key.replace(/[^a-z0-9]/gi, "-");
    const field = el("div", "field");
    const tag = el("label", null, label);
    tag.htmlFor = id;
    const area = el("textarea", "inp");
    area.id = id;
    area.value = form.res[name] || "";
    area.addEventListener("input", function () { form.res[name] = area.value; });
    field.append(tag, area);
    wrap.append(field);
  }
  const id = "res-outcome-" + key.replace(/[^a-z0-9]/gi, "-");
  const field = el("div", "field");
  const tag = el("label", null, "Outcome");
  tag.htmlFor = id;
  const select = el("select", "inp");
  select.id = id;
  for (const value of ["resolved", "unresolved", "partial", "abandoned"]) {
    const option = el("option", null, value);
    option.value = value;
    if ((form.res.outcome || "resolved") === value) option.selected = true;
    select.append(option);
  }
  select.addEventListener("change", function () { form.res.outcome = select.value; });
  field.append(tag, select);
  wrap.append(field);
  return wrap;
}

function actionsFor(pending, form, key) {
  const wrap = el("div", "actions");
  const needClaim = pending.kind !== "resolve" && !(pending.missing || []).length;
  if (needClaim && !form.claim) {
    /* The claim is not decoration: `verify()` raises unless the engineer states it when nothing is
       missing, so the actions stay unavailable until it is answered. */
    wrap.append(el("span", "hint", "Choose whether the evidence supports or contradicts it first."));
    return wrap;
  }

  const send = function (decision) {
    /* The token of the question this form was built for travels with the answer. If the run has moved
       on, the server refuses the answer for naming a question that is no longer open: the page cannot
       answer a different question than the one it is showing. */
    const payload = { question: key, decision: decision, note: form.note || "" };
    if (pending.kind === "resolve") {
      payload.decision = { resolution: {
        action_taken: form.res.action_taken || "",
        observed_result: form.res.observed_result || "",
        root_cause_confirmed: form.res.root_cause_confirmed || null,
        outcome: form.res.outcome || "resolved",
        failed_approaches: [], evidence_refs: []
      } };
    } else {
      payload.claim = form.claim || null;
      payload.confirmed = false;
    }
    post("decision", payload);
  };

  if (pending.kind === "resolve") {
    const submit = el("button", "primary", "Submit resolution");
    submit.type = "button";
    submit.setAttribute("aria-label", "Submit the resolution");
    submit.addEventListener("click", function () { send("resolve"); });
    const decline = el("button", "danger", "Not resolved");
    decline.type = "button";
    decline.setAttribute("aria-label", "Decline to record a resolution");
    decline.addEventListener("click", function () {
      post("decision", { question: key, decision: { resolution: null } });
    });
    wrap.append(submit, decline);
    return wrap;
  }

  for (const option of (pending.options || ["accept", "modify", "reject"])) {
    const button = el("button", option === "accept" ? "primary" : (option === "reject" ? "danger" : ""),
                      option.charAt(0).toUpperCase() + option.slice(1));
    button.type = "button";
    button.dataset.decision = option;
    button.setAttribute("aria-label",
                        option + " " + (pending.hypothesis_ref || "this hypothesis"));
    if (option === "reject") {
      /* Rejecting discards an explanation the agent spent a call on, and nothing downstream can tell an
         accidental click from a considered one, so it takes a second deliberate press. */
      button.addEventListener("click", function () {
        if (state.confirm === key) { state.confirm = null; send(option); }
        else { state.confirm = key; paint(); }
      });
    } else {
      button.addEventListener("click", function () { send(option); });
    }
    wrap.append(button);
  }
  if (state.confirm === key) {
    const box = el("div", "confirm");
    box.append(el("p", null, "Reject " + (pending.hypothesis_ref || "this hypothesis")
      + "? It is recorded as rejected and is kept out of the retained case."));
    const yes = el("button", "danger", "Yes, reject it");
    yes.type = "button";
    yes.setAttribute("aria-label",
                     "Confirm rejection of " + (pending.hypothesis_ref || "this hypothesis"));
    yes.addEventListener("click", function () { state.confirm = null; send("reject"); });
    const no = el("button", null, "Keep it open");
    no.type = "button";
    no.setAttribute("aria-label", "Cancel rejection");
    no.addEventListener("click", function () { state.confirm = null; paint(); });
    const row = el("div", "actions");
    row.append(yes, no);
    box.append(row);
    wrap.append(box);
  }
  return wrap;
}

function paintDecision() {
  const snapshot = state.snapshot;
  const pending = snapshot && snapshot.pending;
  const done = snapshot && snapshot.status !== "running" && !pending;
  const box = el("section", "panel decide" + (pending ? "" : " idle"));
  box.id = "decision";
  box.dataset.panel = "Decision";
  box.dataset.state = pending ? "awaiting" : (done ? "done" : "idle");
  box.dataset.question = pending ? formKey(pending) : "";

  const head = el("header");
  head.append(el("h2", null, pending
    ? (pending.kind === "resolve" ? "Engineer resolution required" : "Engineer decision required")
    : "Engineer decision"));
  head.append(trust("DECISION"));
  box.append(head);
  const body = el("div", "body");
  box.append(body);

  if (!pending) {
    if (done) {
      const recorded = ((snapshot.view && snapshot.view.panels) || []).filter(function (panel) {
        return panel.name === "Decision";
      })[0];
      if (recorded) {
        for (const item of recorded.items) body.append(metaRow(item, recorded.trust));
      }
      body.append(el("div", "empty", snapshot.outcome === "declined"
        ? "The engineer did not report a resolution, so nothing was retained."
        : "Recorded above. Nothing else in this session concludes anything."));
    } else {
      body.append(el("div", "empty", "Nothing is waiting on the engineer."));
    }
    return box;
  }

  const key = formKey(pending);
  if (!state.form[key]) state.form[key] = { claim: "", note: "", res: {} };
  const form = state.form[key];

  const q = el("div", "qcard");
  if (pending.kind === "resolve") {
    q.append(el("div", "qid", "RESOLUTION"));
    q.append(el("div", "qtext",
      "Report what was actually done, or decline and leave the case unresolved."));
  } else {
    q.append(el("div", "qid", pending.hypothesis_ref || "HYPOTHESIS"));
    if (pending.statement) q.append(el("div", "qtext", pending.statement));
  }
  const mismatched = pending.mismatched || [], missing = pending.missing || [];
  if (pending.kind !== "resolve" && mismatched.length + missing.length) {
    /* Conflicts and gaps come before the actions, because they are why the decision is not a formality.
       Evidence the current system lacks is not the same as evidence that contradicts it. */
    const gaps = el("div", "gaps");
    for (const field of mismatched) {
      const gap = el("div", "gap");
      gap.append(el("span", "gk", "differs from the cited past case: "), el("span", "gv", field));
      gaps.append(gap);
    }
    for (const field of missing) {
      const gap = el("div", "gap blocked");
      gap.append(el("span", "gk", "current evidence lacks: "), el("span", "gv", field));
      gaps.append(gap);
    }
    q.append(gaps);
    q.append(el("div", "why", missing.length
      ? "Missing evidence forces the verdict to insufficient_evidence however strong the memory match, "
        + "so what is left to decide is whether to keep or discard the explanation."
      : "Fields the cited past case recorded differ from current evidence. Memory never settles a field "
        + "for the current system; the decision is yours."));
  }
  body.append(q);

  if (pending.kind === "resolve") {
    body.append(resolutionForm(form, key));
  } else {
    const needClaim = !missing.length;
    const fs = el("fieldset", "field");
    const legend = el("legend");
    legend.append(document.createTextNode("Does current evidence "));
    if (needClaim) legend.append(el("span", "need", "require "));
    legend.append(document.createTextNode("this explanation?"));
    fs.append(legend);
    fs.append(claimToggle(form, needClaim));
    fs.append(el("div", "why", needClaim
      ? "The verdict is your reading of the current evidence, and it is required when nothing is missing."
      : "Not required: the verdict is already insufficient_evidence."));
    body.append(fs);
    body.append(noteField(form, key,
      "What you did to this explanation (optional; kept as your note)"));
  }
  body.append(actionsFor(pending, form, key));
  return box;
}

/* ---------------------------------------------------------------- transport */

function paintRail() {
  const rail = document.getElementById("right");
  const existing = document.getElementById("decision");
  if (existing) existing.remove();
  const traceOpen = rail.querySelector("details.trace");
  const wasOpen = traceOpen ? traceOpen.open : false;
  rail.insertBefore(paintDecision(), rail.firstChild);
  const trace = rail.querySelector("details.trace");
  /* An open trace stays open across a re-render; it is content the engineer chose to look at. */
  if (trace && wasOpen) trace.open = true;
  for (const button of rail.querySelectorAll("button")) {
    const key = button.dataset.key;
    if (!key) continue;
    const at = state.copied.get(key) || 0;
    if (Date.now() - at < 1800) {
      button.dataset.copied = "1";
      button.lastChild.textContent = "Copied";
    }
  }
}

async function post(action, payload) {
  if (state.busy) return;
  state.busy = true;
  try {
    const response = await fetch("/api/sessions/" + encodeURIComponent(state.id) + "/" + action, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}) });
    const data = await response.json().catch(function () { return {}; });
    if (!response.ok) {
      state.notice = data.error || ("The server refused that (" + response.status + ").");
      say(state.notice);
    } else {
      state.notice = null;
    }
  } catch (err) {
    state.notice = "Cannot reach the server: " + err;
  } finally {
    state.busy = false;
    paintBanner(state.snapshot);
    poll();
  }
}

/* Repaint only what a change can have touched. */
function paint() {
  paintRail();
}

function render(snapshot, force) {
  state.snapshot = snapshot;
  paintTopBar(snapshot);
  paintBanner(snapshot);
  const view = snapshot ? snapshot.view : null;
  paintCase(view);
  paintTimeline(view, snapshot ? snapshot.pending : null);
  /* Re-render only on a real change. Rebuilding the DOM on every poll would destroy the engineer's text
     selection, focus and scroll offset, and would wipe "Copied" before it could be read. */
  const key = JSON.stringify(snapshot);
  if (force || key !== state.rendered) {
    state.rendered = key;
    paintPanels(view);
    paintRail();
  }
}

async function poll() {
  if (!state.id) return;
  try {
    const response = await fetch("/api/sessions/" + encodeURIComponent(state.id));
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    render(data, false);
    if (data.status === "running") state.timer = setTimeout(poll, 700);
    else if (state.timer) { clearTimeout(state.timer); state.timer = null; }
  } catch (err) {
    const conn = document.getElementById("m-conn");
    clear(conn);
    conn.append(el("i", "dot bad"), document.createTextNode("Disconnected"));
    state.timer = setTimeout(poll, 2000);
  }
}

document.getElementById("start-btn").addEventListener("click", async function () {
  const description = document.getElementById("description").value.trim();
  if (!description) {
    /* Defence in depth: `syncStartButton` keeps this button disabled until a description exists, so a
       click should not reach here. Covers the keyboard, a programmatic click, and a click racing the
       first `input` event. */
    state.notice = "Describe the issue first. The first line is the summary.";
    render(state.snapshot, false);
    const field = document.getElementById("description");
    field.focus();
    return;
  }
  state.notice = null;
  try {
    const response = await fetch("/api/sessions", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description: description }) });
    const data = await response.json().catch(function () { return {}; });
    if (!response.ok) {
      state.notice = data.error || ("The server refused that (" + response.status + ").");
      render(state.snapshot, false);
      return;
    }
    state.id = data.session_id;
    state.rendered = "";
    document.getElementById("start").hidden = true;
    render({ session_id: state.id, status: "running", view: null, pending: null, outcome: "running" },
           true);
    poll();
  } catch (err) {
    state.notice = "Cannot reach the server: " + err;
    render(state.snapshot, false);
  }
});

/* Keeps Start disabled until the description holds non-whitespace content, and keeps `aria-disabled`
   in step with it so the announced state cannot drift from the visual one. */
function syncStartButton() {
  const field = document.getElementById("description");
  const button = document.getElementById("start-btn");
  const ready = field.value.trim().length > 0;
  button.disabled = !ready;
  button.setAttribute("aria-disabled", ready ? "false" : "true");
}

document.getElementById("description").addEventListener("input", function () {
  /* Clear the correction as soon as they start typing. */
  if (state.notice && state.notice.indexOf("Describe the issue first") === 0) {
    state.notice = null;
    render(state.snapshot, false);
  }
  syncStartButton();
});

syncStartButton();
render(null, true);
</script>
</body>
</html>
"""
