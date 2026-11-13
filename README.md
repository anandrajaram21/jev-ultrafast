<img src="docs/banner.svg" alt="Jev Ultrafast · Browser Use × TypeSafe" width="100%" />

> [!NOTE]
> **Jev Ultrafast is now ultrafast mode in [Browser Use](https://github.com/browser-use/browser-use).** Same idea, built in: the agent acts on numbered page elements. Browser Use also handles long multi-step tasks.
>
> Python: `pip install browser-use`, then `Agent(task, mode='ultrafast')` (already the default on the `bu-fast` and `bu-ultrafast` models). Hosted: the `bu-ultrafast` model in Browser Use Cloud.
>
> This repo stays up as the original Browser Use × TypeSafe experiment.

# Jev Ultrafast ⚡

> [!IMPORTANT]
> **BU Ultrafast, Browser Use Cloud's own hosted agent, is live on every plan.** Set `model: "bu-ultrafast"` to use it.
> **[Learn more](https://browser-use.com/ultrafast?utm_source=github&utm_medium=readme&utm_campaign=jev-ultrafast)** · **[Open Browser Use Cloud](https://cloud.browser-use.com?utm_source=github&utm_medium=readme&utm_campaign=jev-ultrafast)**

**A browser agent with a dynamic, indexed action space.**

Give it one goal. [TypeSafe's Jev](https://docs.typesafe.ai/introduction) picks an operation and an element. A small LLM writes text only when the operation is `TYPE_TEXT`.

**Zürich → London on Google Flights in 7.1 seconds.** One natural-language goal, actual text generation, and loading waits included.

<a href="docs/demo.mp4"><img src="docs/demo.gif" alt="A real Google Flights search at 1× speed, with generated city names and dynamic operation/target decisions" width="100%" /></a>

[Watch the MP4](docs/demo.mp4) · [Measurements](docs/performance.md) · [Read the loop](jev_ultrafast/agent.py)

## The action space

Every observation produces a new element table:

```text
[1] button    Change ticket type · Round trip
[2] combobox  Where from?        · San Francisco
[3] combobox  Where to?          · empty
[4] textbox   Departure          · empty
...
```

The operations are `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, and `BLOCKED`. Only supported operations and targets are offered.

```text
                      one TypeSafe request
                     ┌───────────────────────────┐
page → element table → operation                 │
                     │ click_target              │
                     │ type_text_target          │
                     │ select_target, if present │
                     └─────────────┬─────────────┘
                         use the matching target
                                   │
                    CLICK [7] ─────┤──→ browser
                TYPE_TEXT [3] ─────┘
                          ↓
                   small LLM → text → browser
```

Target questions are speculative. If the operation is `CLICK`, only `click_target` can execute. Two decisions, **one network round trip**. Each target head contains only compatible elements. Native dropdown choices carry an observed element/option index.

There are no site-specific action scripts or prepared field strings in the policy. The Flights example supplies a goal and independently verifies the outcome. The screenshot renderer adds labels afterward; it does not drive the browser.

## Try the original demo

```bash
git clone https://github.com/browser-use/jev-ultrafast.git
cd jev-ultrafast
uv sync
cp .env.example .env
# Add TYPESAFE_API_KEY and TEXT_MODEL_API_KEY.
uv run jev
```

Open **http://127.0.0.1:8766** and click **Start demo → Run automatically**. The inspector shows numbered elements, operation probabilities, target probabilities, and executed actions. **Choose next** pauses before execution.

The browser comes from [agent-browser](https://github.com/vercel-labs/agent-browser) (`brew install agent-browser`). It launches its browser and reports the CDP endpoint. Jev then drives that browser over one WebSocket. agent-browser is not called once per step. Run `agent-browser doctor` if the browser does not connect.

`TEXT_MODEL_API_KEY` is an OpenRouter key in the example configuration. The current demo uses `inception/mercury-2.5` with reasoning disabled. Gemini, GLM, and DeepSeek can also use the OpenAI-compatible text helper; configure the appropriate model, endpoint, and reasoning setting.

## Use the original library

```python
from jev_ultrafast import Agent

with Agent(
    "https://www.google.com/travel/flights?hl=en",
    "Find one-way flights from Zurich to London on March 21, 2027, "
    "for one adult in economy. Stop when matching flight options are visible.",
) as agent:
    for state in agent.run():
        print(state["elapsed_ms"], state["status"])
```

Run with `uv run --env-file .env python your_script.py`. The same policy can run a different task:

```bash
uv run --env-file .env python examples/run.py \
  --url https://en.wikipedia.org/wiki/Main_Page \
  --goal 'Find and open the Wikipedia article about Gödel’s incompleteness theorems.'
```

`uv run --env-file .env python examples/flights.py --keep-open` performs the flight search, checks the actual route/date/results, and saves its trace. It does not select or book a flight.

## Use it in other projects

Install the `jev` command once. It needs [agent-browser](https://github.com/vercel-labs/agent-browser) on `PATH`.

```bash
uv tool install git+https://github.com/anandrajaram21/jev-ultrafast
mkdir -p ~/.config/jev-ultrafast && cp .env.example ~/.config/jev-ultrafast/.env  # then add your keys
```

Settings come from the shell first, then the current project's `.env`, then `~/.config/jev-ultrafast/.env`. Run one goal from any project:

```bash
jev run --url https://mysite.local/wp-admin/post-new.php \
  --goal "Create a draft post titled 'Release notes' with one paragraph 'Coming soon.' Do not publish it." \
  --cookies login.json --trace trace.json
```

`--cookies` takes a JSON list of [CDP cookies](https://chromedevtools.github.io/devtools-protocol/tot/Network/#type-CookieParam), set before the first page load. Use it to sign in to a site under test without putting a password in the goal, which is sent to the model providers. For a local WordPress site, WP-CLI can mint short-lived cookies:

```bash
wp eval '$e=time()+900; echo json_encode([
  ["name"=>LOGGED_IN_COOKIE, "value"=>wp_generate_auth_cookie(1,$e,"logged_in"), "url"=>home_url("/")],
  ["name"=>SECURE_AUTH_COOKIE, "value"=>wp_generate_auth_cookie(1,$e,"secure_auth"), "url"=>admin_url()]]);' > login.json
```

Repeat `--goal` for ordered steps. The model sees only the current step (plus the finished ones and the next one for context), so `DONE` means that step is finished; the run ends after the last. Name controls as the page labels them: every step boundary is a judgement, and a goal that says "Edit" for an option labelled "Bulk edit" can stall there.

`jev run` prints each action, each finished step and a summary, then exits 0 when the model chose `DONE`, 1 when it stopped as blocked, and 2 on an error. `DONE` is not proof. Pass `--check '<shell command>'` to make your own check decide: jev runs it after the goals, prints `check passed` or `check failed (exit N): <output>`, and exits 0 or 1 with it. `--keep-open` leaves the tab open, so `agent-browser` can inspect jev's last page (it switches to the newest tab). In Python, pass the same list as `Agent(url, goals, cookies=..., keep_open=...)`. `jev` with no arguments still opens the inspector.

## Why it moves

- **One request per decision cycle.** Operation and target heads share the same observed state.
- **No screenshots in the default agent loop.** Jev consumes structured state. The inspector opts into screenshots; the video uses a separate continuous screencast.
- **One browser call per snapshot.** Read visible controls, their names, values, and text atomically. Keep references to the actual DOM nodes.
- **Validate the selected target.** Clicks check the document, form values, target, and nearby context. Animation alone does not force another prediction. Resolve current geometry and reject covered controls before input.
- **Wait for useful state.** After typing into a combobox, wait for visible suggestions, capped at 200 ms. Other interactions get at most two animation frames or 50 ms. These reads happen after execution is logged.
- **Keep hidden tabs rendering.** Focus emulation prevents background animation throttling without switching Chrome's visible tab.
- **Send visible text, but every nearby control.** Offscreen article bodies and footers do not fill the model context. Controls above or below the view are offered with an `offscreen` flag (all in view, plus the 60 nearest), each with the heading of its box, and the executor scrolls or focuses them into view on use. Row actions shown only on hover or focus are offered when their row is on the page.
- **One text call per form.** The text helper returns the selected field's value and the values of any other empty fields the goal names explicitly; those are typed without further decisions. Fields the goal does not name stay empty.
- **Reuse an interrupted text request.** A generated value survives a stale-page retry only if the entire text-helper input is unchanged.

Every executed target is resolved from an observed node. The executor rechecks page freshness and click occlusion. Model output never becomes selectors, coordinates, shell commands, or executable JavaScript. Text-helper output must parse as a small JSON object before typing.

## Small enough to read

| File | Job |
| --- | --- |
| [agent.py](jev_ultrafast/agent.py) | The complete loop and text-helper handoff |
| [snapshot.js](jev_ultrafast/snapshot.js) | Atomic DOM snapshot, indexed controls, freshness guards |
| [browser.py](jev_ultrafast/browser.py) | Browser connection, current geometry, execution |
| [model.py](jev_ultrafast/model.py) | Dynamic operation/target heads and text generation |
| [questions.py](jev_ultrafast/questions.py) | Model instructions |
| [demo.py](jev_ultrafast/demo.py) | Local inspector |
| [cli.py](jev_ultrafast/cli.py) | `jev run` for any site and goal |

## Evidence and limits

The current video is a **7,073 ms** Google Flights run. Timing starts after initial page observation and includes model calls, generated text, browser work, stale decisions, and loading waits. A fresh independent check verified the one-way setting, Zürich, London, September 20, 2026, and visible flight options. That date has passed, so the default goal now uses March 21, 2027; Google Flights cannot select past dates. The video plays at 1×, with no opening hold and a 0.5-second final hold.

In six alternating runs with identical models and settings, both versions passed **3/3**. Median task time went from **9.450 s → 7.092 s**, a **25% reduction**; median browser protocol calls went from **1,092 → 101**. This is three repeats of one task on one browser profile, not a general reliability benchmark.

The same policy opened the requested Wikipedia article in **2.798 s** and passed a local hotel search/filter task in **1.896 s**. Runs, failures, source hashes, and measurement boundaries are in [performance.md](docs/performance.md).

A `DONE` choice still requires independent outcome verification. The DOM reader handles common HTML and ARIA controls, not the full accessible-name specification. Same-origin iframes, such as the WordPress block editor canvas, are read and driven in place; cross-origin frames stay opaque. Enter is available in a focused text field. Shadow roots, canvas, uploads, pop-up tabs, nested scrolling, and other keyboard widgets remain outside this MVP. A decision that goes stale five times in a row without any action stops the run as blocked. Owned tabs share agent-browser's browser profile.

## Development

```bash
uv run ruff check .
uv run pytest
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```

Tests are offline. `uv run python scripts/check_guards.py` checks real controls in a local browser without model calls. Live examples and recording scripts make paid API calls. `scripts/record_flights.py <new-folder>` captures original browser timestamps; `scripts/render_demo.py <recording-folder>` renders that verified run at 1× and crops out the Google account strip. Credentials and raw traces stay ignored.

---

[Browser Use](https://github.com/browser-use/browser-use) · [agent-browser](https://github.com/vercel-labs/agent-browser) · [TypeSafe speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)
