from dop.core.state import (
    default_state, ensure_state_defaults,
    record_e2e_run, reset_strikes, get_strike_count,
)

def test_default_state_has_e2e_and_runtime():
    s = default_state("OG-100", "https://jira/OG-100")
    assert "e2e" in s
    assert s["e2e"]["max_strikes"] == 3
    assert s["e2e"]["strike_count"] == 0
    assert s["e2e"]["history"] == []
    assert s["e2e"]["last_run"] is None
    assert "runtime" in s
    assert s["runtime"]["apps_up"] == []

def test_ensure_state_defaults_v04_compat():
    old_state = {
        "jiraKey": "OG-100",
        "jiraUrl": "https://jira/OG-100",
        "repos": {},
        "prs": [],
        "commands_log": [],
    }
    ensure_state_defaults(old_state)
    assert "e2e" in old_state
    assert old_state["e2e"]["max_strikes"] == 3

def test_record_e2e_run_increments_strike_on_red():
    s = default_state("OG-100", "https://jira/OG-100")
    record_e2e_run(s, status="red", exit_code=1, suites_run=["osf"], filter_used="og_100")
    assert s["e2e"]["strike_count"] == 1
    assert len(s["e2e"]["history"]) == 1

def test_record_e2e_run_resets_strike_on_green():
    s = default_state("OG-100", "https://jira/OG-100")
    s["e2e"]["strike_count"] = 2
    record_e2e_run(s, status="green", exit_code=0, suites_run=["osf"], filter_used="og_100")
    assert s["e2e"]["strike_count"] == 0

def test_reset_strikes():
    s = default_state("OG-100", "https://jira/OG-100")
    s["e2e"]["strike_count"] = 3
    reset_strikes(s)
    assert s["e2e"]["strike_count"] == 0

def test_get_strike_count():
    s = default_state("OG-100", "https://jira/OG-100")
    assert get_strike_count(s) == (0, 3)
    s["e2e"]["strike_count"] = 2
    s["e2e"]["max_strikes"] = 5
    assert get_strike_count(s) == (2, 5)

def test_e2e_history_caps_at_10():
    s = default_state("OG-100", "https://jira/OG-100")
    for i in range(12):
        record_e2e_run(s, status="red", exit_code=1, suites_run=["osf"], filter_used="og_100")
    assert len(s["e2e"]["history"]) == 10
