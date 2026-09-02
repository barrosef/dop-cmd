import argparse
from unittest.mock import MagicMock, patch

from dop.config.schema import (
    WorkspaceConfig, RuntimeConfig, AppConfig,
    DockerComposeConfig, EphemeralRunnerConfig,
)
from dop.runtime import handlers
from dop.runtime.handlers import _merge_allure_results


def _ws(root) -> WorkspaceConfig:
    apps = {
        "optum-support-be": AppConfig(name="optum-support-be", service="optum-support-be",
                                      role="backend", port=8080),
        "optum-support-fe": AppConfig(name="optum-support-fe", service="optum-support-fe",
                                      role="frontend", port=5173,
                                      depends_on=["optum-support-be"],
                                      e2e_suite="optum-support-fe"),
    }
    dc = DockerComposeConfig(
        ephemeral_runner=EphemeralRunnerConfig(service="playwright-env", profile="e2e"),
    )
    rt = RuntimeConfig(apps=apps, infra=["mongodb", "allure"], docker_compose=dc)
    return WorkspaceConfig(name="t", root=str(root), runtime=rt)


# ---- P1: merge/aggregate allure results ----

def test_merge_accumulates(tmp_path):
    src = tmp_path / "run-1" / "results"
    src.mkdir(parents=True)
    (src / "b-result.json").write_text("{}")
    dest = tmp_path / ".allure-results"
    dest.mkdir()
    (dest / "a-result.json").write_text("{}")

    n = _merge_allure_results(src, dest, fresh=False)
    assert n == 1
    assert {f.name for f in dest.iterdir()} == {"a-result.json", "b-result.json"}


def test_merge_fresh_resets(tmp_path):
    src = tmp_path / "run-1" / "results"
    src.mkdir(parents=True)
    (src / "b-result.json").write_text("{}")
    dest = tmp_path / ".allure-results"
    dest.mkdir()
    (dest / "a-result.json").write_text("{}")

    n = _merge_allure_results(src, dest, fresh=True)
    assert n == 1
    assert {f.name for f in dest.iterdir()} == {"b-result.json"}


def test_merge_missing_source(tmp_path):
    src = tmp_path / "nope" / "results"
    dest = tmp_path / ".allure-results"
    n = _merge_allure_results(src, dest, fresh=False)
    assert n == 0
    assert dest.is_dir()  # criado mesmo sem fonte


# ---- P3: host pre-creates run dir (host-owned) ----

def test_handle_e2e_precreates_host_run_dir(tmp_path):
    ws = _ws(tmp_path)
    (tmp_path / "e2e" / "optum-support-fe" / "tests").mkdir(parents=True)

    provider = MagicMock()
    provider.run_ephemeral.return_value = 0
    args = argparse.Namespace(targets=["optum-support-fe"], k=None, headed=False, fresh_report=False)

    # dry_run=False para exercitar a criação real do dir; allure não está instalado,
    # mas como não há resultados a geração é pulada (sem chamar o binário).
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider):
        rc = handlers.handle_e2e(ws, args, dry_run=False)

    assert rc == 0
    run_results = tmp_path / "e2e" / "reports" / "manual" / "optum-support-fe" / "run-1" / "results"
    assert run_results.is_dir(), "host deve pré-criar run-N/results (P3)"


def test_handle_e2e_aggregates_run_into_suite(tmp_path):
    """P1 ponta-a-ponta: o resultado da run é copiado para <suite>/.allure-results."""
    ws = _ws(tmp_path)
    (tmp_path / "e2e" / "optum-support-fe" / "tests").mkdir(parents=True)

    def fake_run(args, *, env, dry_run=False, logger=None):
        # simula o container gravando um result no alluredir (run-1/results)
        rd = tmp_path / "e2e" / "reports" / "manual" / "optum-support-fe" / "run-1" / "results"
        (rd / "x-result.json").write_text("{}")
        return 0

    provider = MagicMock()
    provider.run_ephemeral.side_effect = fake_run
    args = argparse.Namespace(targets=["optum-support-fe"], k=None, headed=False, fresh_report=False)

    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider):
        rc = handlers.handle_e2e(ws, args, dry_run=False)

    assert rc == 0
    aggregated = tmp_path / "e2e" / "optum-support-fe" / ".allure-results" / "x-result.json"
    assert aggregated.is_file(), "P1: resultado da run deve ser agregado no .allure-results da suíte"


# ---- Self-healing: acumulador é estado derivado, reconstruível das runs ----

def test_suite_run_results_scans_all_jiras_sorted(tmp_path):
    from dop.runtime.handlers import _suite_run_results
    rr = tmp_path / "reports"
    for jira, n in [("SUOPT-2", 1), ("SUOPT-1", 2), ("SUOPT-1", 1)]:
        (rr / jira / "s" / f"run-{n}" / "results").mkdir(parents=True)
    (rr / "SUOPT-1" / "other-suite" / "run-9" / "results").mkdir(parents=True)
    (rr / ".hidden" / "s" / "run-3" / "results").mkdir(parents=True)
    (rr / "SUOPT-1" / "s" / "run-x").mkdir(parents=True)  # não é run-N válido

    got = _suite_run_results(rr, "s")
    rel = [str(p.relative_to(rr)) for p in got]
    assert rel == [
        "SUOPT-1/s/run-1/results",
        "SUOPT-1/s/run-2/results",
        "SUOPT-2/s/run-1/results",
    ]


def test_handle_e2e_selfheals_accumulator_from_all_runs(tmp_path):
    """Wipe do .allure-results é reparado: a publicação agrega TODAS as runs."""
    ws = _ws(tmp_path)
    (tmp_path / "e2e" / "optum-support-fe" / "tests").mkdir(parents=True)
    # run antiga de outra demanda, cujo resultado sumiu do acumulador
    old = tmp_path / "e2e" / "reports" / "SUOPT-1" / "optum-support-fe" / "run-1" / "results"
    old.mkdir(parents=True)
    (old / "old-result.json").write_text("{}")

    def fake_run(args, *, env, dry_run=False, logger=None):
        rd = tmp_path / "e2e" / "reports" / "manual" / "optum-support-fe" / "run-1" / "results"
        (rd / "new-result.json").write_text("{}")
        return 0

    provider = MagicMock()
    provider.run_ephemeral.side_effect = fake_run
    args = argparse.Namespace(targets=["optum-support-fe"], k=None, headed=False, fresh_report=False)

    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider):
        rc = handlers.handle_e2e(ws, args, dry_run=False)

    assert rc == 0
    acc = tmp_path / "e2e" / "optum-support-fe" / ".allure-results"
    names = {f.name for f in acc.iterdir()}
    assert {"old-result.json", "new-result.json"} <= names


def test_publish_keeps_served_report_when_generate_fails(tmp_path, monkeypatch):
    """Falha no allure generate não pode derrubar o report já servido (swap atômico)."""
    from dop.runtime.handlers import _publish_allure_project
    src = tmp_path / "run-1" / "results"
    src.mkdir(parents=True)
    (src / "a-result.json").write_text("{}")
    results = tmp_path / ".allure-results"
    reports_root = tmp_path / "reports"
    served = reports_root / "e2e-s"
    served.mkdir(parents=True)
    (served / "index.html").write_text("old report")

    def boom(*a, **k):
        raise RuntimeError("allure not available")
    monkeypatch.setattr(handlers, "run_command", boom)

    _publish_allure_project(
        project="e2e-s", results_dir=results, reports_root=reports_root,
        src=[src], report_name="e2e-s",
    )

    assert (served / "index.html").read_text() == "old report"
    assert not (reports_root / ".e2e-s.tmp-gen").exists()
