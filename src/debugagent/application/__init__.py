"""Application layer: a UI that sits ON TOP of the architecture, not inside it.

Everything here is built from two seams the investigation already had:

  1. **`Engineer`** - the protocol `investigate()` takes for `show` / `current_facts` / `decide` /
     `resolve`. A UI is another implementation of it, a sibling of the CLI's engineer.
  2. **`Session`** - the record of what happened, already projected into `to_dict()`.

Neither seam was widened for this milestone. There is no `if web_ui:` in `investigate()`, no UI import
anywhere in `agents/`, `pipeline/`, `memory/` or `llm/`, and a test asserts the core stays domain-blind
and UI-blind.

    Session  ->  SessionViewModel  ->  browser
                      ^
                      |
        WebEngineer implements Engineer

## Domain neutrality is structural

`view_model.py` imports nothing from `debugagent.domains` and evaluates a session through its
attributes alone. A VLSI session and a generic debugging session occupy the same seven panel slots;
which ones are populated differs because the sessions differ, not because the view model knows what a
domain is. Adding a second domain therefore requires no change here.

## UI-1 limitations, stated rather than discovered

  - **The decision loop is synchronous.** The investigation blocks in `decide()` / `resolve()` until the
    browser answers. The session runs on its own thread so the engineer can read while it works, but the
    request that would answer stays active for as long as the engineer takes. Suspend/resume is out of
    scope: it would turn `investigate()` into a resumable state machine.
  - **Local only.** The server binds 127.0.0.1 and has no authentication, no CSRF protection and no
    rate limiting. It is a local engineering tool, not a service.
  - **In-memory sessions.** A session lives in the server process and is lost on restart.
  - **The browser is untrusted input.** It may supply facts, a decision and a resolution, and each goes
    through the existing validators. It cannot write evidence, set a verification status, or apply
    anything.
"""

from debugagent.application.view_model import (
    PANEL_ORDER,
    TRUST_LEVELS,
    CaseView,
    EnvironmentFact,
    Panel,
    PanelItem,
    SessionViewModel,
    ViewModelError,
    build_view_model,
)
from debugagent.application.web_engineer import (
    ENGINEER_DECISIONS,
    DecisionRequest,
    DecisionTimeout,
    WebEngineer,
)
from debugagent.application.web_server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    SessionRecord,
    SessionRegistry,
    make_server,
    serve,
)
from debugagent.application.host import make_session_factory, parse_issue

__all__ = [
    "Panel",
    "PanelItem",
    "CaseView",
    "EnvironmentFact",
    "SessionViewModel",
    "ViewModelError",
    "TRUST_LEVELS",
    "PANEL_ORDER",
    "build_view_model",
    "WebEngineer",
    "DecisionRequest",
    "DecisionTimeout",
    "ENGINEER_DECISIONS",
    "SessionRecord",
    "SessionRegistry",
    "make_server",
    "serve",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "make_session_factory",
    "parse_issue",
]