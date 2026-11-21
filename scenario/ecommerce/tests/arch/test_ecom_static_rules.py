"""R3, R8 (SQL part), R21 (file part), R22, R23: checks over the repository, no database needed."""
import subprocess

import pytest
import yaml

import ecom_helpers as H


def _sql_files():
    files = H.read_sql_files()
    layers = {k.split("/")[0] for k in files}
    missing = {"staging", "intermediate", "marts", "quarantine"} - layers
    assert not missing, f"no SQL under scenario/ecommerce/sql/ for layers {sorted(missing)}"
    return files


def test_r3_layer_references_only_upstream():
    files = _sql_files()
    assert any("raw." in H.strip_sql_comments(t).lower() for k, t in files.items() if k.startswith("staging/")), "staging must read raw"
    assert H.check_sql_layering(files) == []


def test_r3_only_staging_touches_raw():
    for rel, text in _sql_files().items():
        if rel.split("/")[0] in ("intermediate", "marts", "quarantine"):
            assert "raw." not in H.strip_sql_comments(text).lower().replace(" ", ""), rel


def test_r8_sql_has_no_nondeterministic_functions():
    assert H.check_sql_deterministic(_sql_files()) == []


def test_r21_contract_files_classify_every_mart_column():
    files = sorted(H.CONTRACTS_DIR.glob("*.yaml"))
    assert files, "no scenario/ecommerce/contracts/*.yaml"
    docs = [yaml.safe_load(p.read_text()) for p in files]
    arch = [("daily_country_report", c) for c, _ in H.REPORT_COLUMNS] + \
           [("daily_country_report_dq", c) for c in ["report_date", "country"] + H.DQ_COLUMNS]
    assert H.check_contract_columns(docs, arch) == []
    found = {}
    for d in docs:
        found.update(H.collect_column_classes(d))
    def cls(c):
        return {str(e["classification"]).lower() for e in found[c]}
    assert cls("gross_revenue") == {"confidential"}           # architecture: confidential (financial)
    for c in ("report_date", "country", "total_orders", "completed_orders", "successful_payments", "failed_payments", "data_quality_status"):
        assert cls(c) == {"internal"}, c


def test_r22_no_credentials_cloud_or_network_code_in_scenario():
    files = H.scenario_code_files()
    assert any(k.startswith("src_scenario/") and k.endswith(".py") for k in files), "src_scenario has no python files yet"
    assert H.check_forbidden(files) == []


def _git(*args):
    return subprocess.run(["git", *args], cwd=H.ROOT, capture_output=True, text=True)


def test_r23_frozen_paths_unchanged_vs_baseline_tag():
    if _git("rev-parse", "--verify", "e2e-baseline").returncode != 0:
        pytest.skip("git tag e2e-baseline not available in this checkout; cannot evaluate R23 here")
    tracked = _git("diff", "--stat", "e2e-baseline", "--", "src", "tests", "evals", ".claude", "scenario/ecommerce/data/raw")
    assert tracked.returncode == 0, tracked.stderr
    assert tracked.stdout.strip() == "", tracked.stdout
    untracked = _git("ls-files", "--others", "--exclude-standard", "--", "src", "tests", "evals", ".claude", "scenario/ecommerce/data/raw")
    assert untracked.stdout.strip() == "", f"untracked files in frozen paths:\n{untracked.stdout}"


@pytest.mark.parametrize("name,digest", sorted(H.RAW_SHA256.items()))
def test_r23_raw_inputs_have_recorded_sha256(name, digest):
    assert H.sha256_file(H.RAW_DIR / name) == digest
