import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_metrics_module():
    path = _repo_root() / "scripts" / "dev" / "maintainability_metrics.py"
    spec = importlib.util.spec_from_file_location("maintainability_metrics", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_maintainability_ratchet_matches_current_baseline():
    module = _load_metrics_module()
    metrics = module.collect_metrics(_repo_root(), limit=3)
    ratchet_budget_actuals = {
        name: getattr(metrics, name) for name in module.DEFAULT_RATCHET_BUDGETS
    }
    expected = module.DEFAULT_RATCHET_BUDGETS
    assert ratchet_budget_actuals == expected, (
        f"Ratchet drift! {ratchet_budget_actuals} != {expected}"
    )
    assert module.check_ratchet_budgets(metrics) == {}


def test_maintainability_metrics_cli_outputs_json():
    repo_root = _repo_root()
    result = subprocess.run(
        ["python", "scripts/dev/maintainability_metrics.py", "--json", "--limit", "2"],
        cwd=repo_root, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["thresholds"]["large_file_lines"] == 800
    assert "files_over_800" in payload
    assert len(payload["largest_functions"]) == 2


def test_maintainability_metrics_ratchet_flag_passes():
    repo_root = _repo_root()
    result = subprocess.run(
        ["python", "scripts/dev/maintainability_metrics.py", "--ratchet"],
        cwd=repo_root, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"Ratchet flag failed:\n{result.stderr}"
