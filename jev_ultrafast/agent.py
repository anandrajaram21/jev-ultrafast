"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import time
from pathlib import Path

from .browser import Browser, StalePage
from .model import action_space, choose, field_context, field_text
from .questions import MAX_STEPS

STALE_LIMIT = 5


def step_goal(plan, index):
    """The model sees only the current step of an ordered plan, so DONE means that step is finished."""
    if len(plan) == 1:
        return plan[0]
    finished = "".join(f"\n{i + 1}. {step}" for i, step in enumerate(plan[:index]))
    following = plan[index + 1] if index + 1 < len(plan) else None
    return (
        f"Step {index + 1} of {len(plan)}: {plan[index]}"
        + (f"\n\nAlready finished, do not redo:{finished}" if finished else "")
        + (f"\n\nNext step, for context only; do not start it: {following}" if following else "")
        + "\n\nDo every part of this step, then choose DONE as soon as it is visibly finished"
        + (" (the page is ready for the next step)." if following else ".")
    )


class Agent:
    def __init__(self, url, goals, *, record_dir=None, screenshots=False, cookies=None, keep_open=False):
        plan = [goals.strip()] if isinstance(goals, str) else [g.strip() for g in goals if g.strip()]
        if not plan or not plan[0]:
            raise ValueError("Supply a task")
        self.pending_text = None
        self.keep_open = keep_open
        self.browser = Browser(url, cookies=cookies)
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.browser.close()
            raise
        self.state = dict(
            browser=self.browser,
            goal=step_goal(plan, 0),
            page=page,
            decision=None,
            history=[],
            status="ready",
            plan=plan,
            plan_index=0,
            steps_done=[],
            decisions=[],
            text_calls=[],
            elapsed_ms=0,
            started_at=None,
            record=bool(self.record_dir),
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            try:
                self.command("predict", {})
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                state["decision"] = None
                state["status"] = "ready"
                # A target that can never be reached must not spend the whole call budget.
                last = [d["choice"] for d in state["decisions"][-STALE_LIMIT:]]
                if len(last) == STALE_LIMIT and len(set(last)) == 1 and last[0] not in {"DONE", "BLOCKED"} and (
                    not state["history"] or state["history"][-1]["executed_ms"] < state["decisions"][-STALE_LIMIT][
                        "elapsed_ms"]
                ):
                    state["status"] = "blocked"
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            if len(state["decisions"]) >= MAX_STEPS * 2:
                raise ValueError("Reached the demo's model-call budget")
            state["decision"] = choose(state["page"], state["goal"], state["history"])
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            state["status"] = "predicted"
        elif name == "act":
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                if selected == "BLOCKED":
                    state["status"] = "blocked"
                    return self.snapshot()
                state["steps_done"].append({"step": state["plan_index"] + 1, "elapsed_ms": state["elapsed_ms"]})
                state["plan_index"] += 1
                if state["plan_index"] < len(state["plan"]):
                    state["goal"] = step_goal(state["plan"], state["plan_index"])
                    state["status"] = "ready"
                else:
                    state["status"] = "done"
                return self.snapshot()
            action = next(a for a in page["actions"] if a["id"] == selected)
            if len(state["history"]) >= MAX_STEPS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped at the {MAX_STEPS}-action demo budget")
            text, helper, others = None, None, {}
            if action["kind"] == "fill":
                if not state["browser"].fresh(page):
                    raise StalePage("Page changed before text generation. Choose again.")
                context = field_context(state["goal"], action, page, state["history"])
                if self.pending_text and self.pending_text[0] == context:
                    _, text, helper, others = self.pending_text
                else:
                    text, helper, others = field_text(context)
                    self.pending_text = (context, text, helper, others)
                    state["text_calls"].append({**helper, "field": action["label"], "value": text, "others": others})
            # Browser.act checks freshness immediately before input, including after text generation.
            state["browser"].act(action, page, text=text)
            self.pending_text = None
            self.record(action, page, decision, text, helper)
            # The same helper answer named values for other empty fields; type them without another decision.
            nodes = {a["id"]: a["node"] for a in page["actions"] if a["kind"] == "fill"}
            for field_id, value in others.items():
                current = state["page"]
                follow = next(
                    (a for a in current["actions"] if a["kind"] == "fill" and a["node"] == nodes.get(field_id)), None
                )
                if follow is None or follow.get("value"):
                    continue
                try:
                    state["browser"].act(follow, current, text=value)
                except StalePage:
                    break
                follow_decision = {**decision, "choice": follow["id"], "probabilities": {follow["id"]: None},
                                   "latency_ms": 0, "target": None, "usage": {}}
                # The text call was already counted on the selected field.
                self.record(follow, current, follow_decision, value, {**helper, "latency_ms": 0})
            repeated = state["history"][-3:]
            state["status"] = (
                "blocked"
                if len(repeated) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated)
                else "ready"
            )
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def record(self, action, page, decision, text, helper):
        """Log an executed action, then observe its result. A stale observation must not erase the action."""
        state = self.state
        selected = decision["choice"]
        state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
        state["history"].append(
            {
                "step": len(state["history"]) + 1,
                "action": action["label"],
                "kind": action["kind"],
                "choice": selected,
                "probability": decision["probabilities"][selected],
                "confidence": decision["confidence"],
                "latency_ms": decision["latency_ms"],
                "text": text,
                "text_helper": helper["model"] if helper else None,
                "text_latency_ms": helper["latency_ms"] if helper else 0,
                "operation": decision["operation"],
                "target": decision["target"],
                "page_changed": None,
                "url": page["url"],
                "usage": decision["usage"],
                "executed_ms": state["elapsed_ms"],
                "elapsed_ms": state["elapsed_ms"],
            }
        )
        state["page"] = state["browser"].observe(screenshot=self.screenshots)
        state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
        state["history"][-1].update(
            page_changed=state["page"]["fingerprint"] != page["fingerprint"],
            url=state["page"]["url"],
            elapsed_ms=state["elapsed_ms"],
        )
        if state["record"]:
            (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                base64.b64decode(state["page"]["screenshot"])
            )

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.command("tick")

    def close(self):
        if not self.keep_open:
            self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
