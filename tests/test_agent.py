"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import os
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "plan": ["Find a book"],
        "plan_index": 0,
        "steps_done": [],
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}, {}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}, {}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import jev_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2027-03-21",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Mar 21"),
                ("Nonstop flight on Sunday, March 21. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2027", "2028")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


def test_login_cookies_are_set_before_the_first_navigation(monkeypatch):
    import jev_ultrafast.browser as browser

    calls = []

    def cdp(method, session_id=None, **params):
        calls.append(method)
        return {"targetId": "t", "sessionId": "s", "result": {"value": "complete"}}

    monkeypatch.setattr(browser, "cdp", cdp)
    cookies = [{"name": "session", "value": "secret", "domain": "site.test", "path": "/"}]
    browser.Browser("https://site.test/admin", cookies=cookies)
    assert calls.index("Network.setCookies") < calls.index("Page.navigate")


def test_environment_prefers_shell_then_project_then_user_config(monkeypatch, tmp_path):
    from jev_ultrafast import demo

    config = tmp_path / "config.env"
    config.write_text("JEV_A=config\nJEV_B=config\nJEV_C=config\n")
    (tmp_path / ".env").write_text('JEV_B="project"\nJEV_C=project\n')
    monkeypatch.setattr(demo, "CONFIG", config)
    monkeypatch.chdir(tmp_path)
    for key in ("JEV_A", "JEV_B"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JEV_C", "shell")
    demo.load_environment()
    assert (os.environ["JEV_A"], os.environ["JEV_B"], os.environ["JEV_C"]) == ("config", "project", "shell")


@pytest.mark.parametrize("final, failure, code", [("done", None, 0), ("blocked", None, 1), ("ready", "HTTP 404", 2)])
def test_run_command_reports_status_and_writes_a_trace(monkeypatch, tmp_path, capsys, final, failure, code):
    from jev_ultrafast import cli

    state = {"status": final, "elapsed_ms": 4277, "decisions": [{}] * 6, "text_calls": [{}] * 2,
             "history": [{"action": "Save draft", "operation": "CLICK", "elapsed_ms": 3675}],
             "page": {"url": "https://site.test/done"}}
    received = {}

    class FakeAgent:
        def __init__(self, url, goals, cookies=None, keep_open=False):
            received.update(url=url, goals=goals, cookies=cookies)

        def run(self):
            if failure:
                raise RuntimeError(failure)
            yield state

        def snapshot(self):
            return state

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(cli, "Agent", FakeAgent)
    monkeypatch.setattr(cli.demo, "load_environment", lambda: None)
    cookies, trace = tmp_path / "cookies.json", tmp_path / "trace.json"
    cookies.write_text('[{"name": "session", "value": "secret", "url": "https://site.test"}]')
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["run", "--url", "https://site.test", "--goal", "First", "--goal", "Second",
                  "--cookies", str(cookies), "--trace", str(trace)])
    assert exit_info.value.code == code
    assert received == {"url": "https://site.test", "goals": ["First", "Second"],
                        "cookies": [{"name": "session", "value": "secret", "url": "https://site.test"}]}
    assert json.loads(trace.read_text())["status"] == final
    out = capsys.readouterr().out
    assert "8 model calls" in out
    assert out.count("Save draft") == (0 if failure else 1)


def test_done_finishes_only_the_current_step_of_an_ordered_plan(runner):
    runner.state["plan"] = ["Tick Dune.", "Click View post, then click the Fantasy link."]
    runner.state["goal"] = loop.step_goal(runner.state["plan"], 0)
    assert runner.state["goal"].startswith("Step 1 of 2: Tick Dune.")
    assert "Next step, for context only; do not start it: Click View post" in runner.state["goal"]
    runner.state["decision"] = decision("DONE")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "ready" and runner.state["plan_index"] == 1
    assert "Step 2 of 2: Click View post, then click the Fantasy link." in runner.state["goal"]
    assert "1. Tick Dune." in runner.state["goal"]
    runner.state["decision"] = decision("DONE")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "done" and [s["step"] for s in runner.state["steps_done"]] == [1, 2]


def test_a_single_goal_is_sent_unchanged():
    assert loop.step_goal(["Find a book"], 0) == "Find a book"


def form():
    state = page()
    state["actions"] = [
        {"id": "e1", "kind": "fill", "label": "Title", "role": "textbox", "value": "", "node": 1},
        {"id": "e2", "kind": "fill", "label": "ISBN", "role": "textbox", "value": "", "node": 2},
        {"id": "e3", "kind": "fill", "label": "Pages", "role": "spinbutton", "value": "", "node": 3},
        {"id": "e4", "kind": "fill", "label": "Notes", "role": "textbox", "value": "kept", "node": 4},
    ]
    state["fingerprint"] = fingerprint(state)
    return state


def test_one_text_answer_fills_the_other_named_empty_fields(runner, monkeypatch):
    p = form()
    runner.state["page"] = p
    runner.state["browser"].observe.return_value = p
    helper = Mock(return_value=("Mort", {"model": "test", "latency_ms": 10}, {"e3": "272", "e4": "x"}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.command("act", {"fingerprint": p["fingerprint"]})
    typed = [(c.args[0]["id"], c.kwargs["text"]) for c in runner.state["browser"].act.call_args_list]
    assert typed == [("e1", "Mort"), ("e3", "272")]  # e4 already has a value and is never overwritten.
    assert helper.call_count == 1 and [h["text"] for h in runner.state["history"]] == ["Mort", "272"]


def test_follow_up_fill_stops_on_a_stale_page(runner, monkeypatch):
    p = form()
    runner.state["page"] = p
    runner.state["browser"].observe.return_value = p
    runner.state["browser"].act.side_effect = [None, StalePage("changed")]
    monkeypatch.setattr(loop, "field_text", Mock(return_value=("Mort", {"model": "t", "latency_ms": 1},
                                                                {"e2": "1", "e3": "2"})))
    runner.command("act", {"fingerprint": p["fingerprint"]})
    assert runner.state["browser"].act.call_count == 2 and len(runner.state["history"]) == 1


def test_field_context_offers_other_empty_fields_only():
    p = form()
    context = model.field_context("Add Mort", p["actions"][0], p, [])
    assert [f["id"] for f in context["other_empty_fields"]] == ["e2", "e3"]


@pytest.mark.parametrize("content, others", [
    ('{"text":"Mort","other_fields":{"e3":"272","e2":null}}', {"e3": "272"}),
    ('{"text":"Mort"}', {}),
])
def test_text_helper_returns_named_values_for_other_fields(monkeypatch, content, others):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    context = model.field_context("Add Mort", form()["actions"][0], form(), [])
    assert model.field_text(context)[2] == others


def test_text_helper_rejects_values_for_fields_it_was_not_offered(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    content = '{"text":"Mort","other_fields":{"e99":"x"}}'
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text(model.field_context("Add Mort", form()["actions"][0], form(), []))


def test_enter_is_a_key_event_not_a_click(monkeypatch):
    import jev_ultrafast.browser as browser

    cdp = Mock(return_value={})
    monkeypatch.setattr(browser, "cdp", cdp)
    browser_operation({"operation": "act", "session": "s", "action": {"id": "press_enter", "kind": "key"}})
    assert [c.kwargs["type"] for c in cdp.call_args_list] == ["keyDown", "keyUp"]
    assert cdp.call_args_list[0].kwargs["key"] == "Enter"


def test_offscreen_and_section_reach_the_target_head():
    actions = [{"id": "e1", "kind": "click", "label": "Fantasy", "role": "checkbox", "node": 1,
                "section": "Genres", "offscreen": "below"}]
    elements, targets, _ = model.action_space(actions)
    assert elements[0]["section"] == "Genres" and elements[0]["offscreen"] == "below"


@pytest.mark.parametrize("command, code", [("true", 0), ("echo missing; false", 1)])
def test_check_decides_the_exit_code(monkeypatch, capsys, command, code):
    from jev_ultrafast import cli

    state = {"status": "done", "elapsed_ms": 1, "decisions": [], "text_calls": [], "history": [],
             "page": {"url": "https://site.test"}}

    class FakeAgent:
        def __init__(self, *_args, **kwargs):
            assert kwargs["keep_open"] is True

        def run(self):
            yield state

        def snapshot(self):
            return state

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(cli, "Agent", FakeAgent)
    monkeypatch.setattr(cli.demo, "load_environment", lambda: None)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["run", "--url", "https://site.test", "--goal", "Go", "--check", command, "--keep-open"])
    assert exit_info.value.code == code
    assert ("check passed" if code == 0 else "check failed (exit 1): missing") in capsys.readouterr().out


def test_an_unreachable_target_stops_as_blocked_instead_of_spending_the_budget(runner):
    runner.state["status"] = "ready"
    runner.state["browser"].act.side_effect = StalePage("covered")
    runner.command = loop.Agent.command.__get__(runner)
    for _ in range(loop.STALE_LIMIT):
        runner.state["decisions"].append({"choice": "e3", "elapsed_ms": 0})
    runner.state["decision"] = decision("e3")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    import unittest.mock as um
    with um.patch.object(loop, "choose", return_value={**decision("e3"), "probabilities": {"e3": 1.0}}):
        runner.command("tick")
    assert runner.state["status"] == "blocked"
