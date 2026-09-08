"""Phase 4.3: validate dashboards and Prometheus scrape config."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from prometheus_client import REGISTRY

import app.observability  # noqa: F401  (registers llm_gateway_* metrics)

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = ROOT / "infra" / "grafana" / "dashboards"
PROMETHEUS_YML = ROOT / "infra" / "prometheus" / "prometheus.yml"


def _dashboards():
    return list(DASHBOARD_DIR.glob("*.json"))


def _exported_metric_names():
    names: set[str] = set()
    for metric in REGISTRY.collect():
        names.add(metric.name)
        if metric.type == "counter":
            names.add(metric.name + "_total")
        elif metric.type == "histogram":
            names.update(
                {metric.name + "_bucket", metric.name + "_sum", metric.name + "_count"}
            )
    return names


def test_three_dashboards_present():
    titles = {json.loads(p.read_text())["title"] for p in _dashboards()}
    assert len(titles) == 3
    assert "LLM Gateway - Operations" in titles
    assert "LLM Gateway - Business" in titles
    assert "LLM Gateway - Performance" in titles


def test_dashboards_have_required_fields():
    for path in _dashboards():
        data = json.loads(path.read_text())
        assert data["uid"]
        assert data["title"]
        assert data["panels"]
        assert data["schemaVersion"] == 39


def test_dashboard_queries_reference_defined_metrics():
    available = _exported_metric_names()
    referenced: set[str] = set()
    for path in _dashboards():
        data = json.loads(path.read_text())
        for panel in data["panels"]:
            for target in panel.get("targets", []):
                referenced.update(re.findall(r"llm_gateway_[a-z_]+", target["expr"]))
    assert referenced
    missing = referenced - available
    assert not missing, f"metrics not defined: {sorted(missing)}"


def test_prometheus_scrape_config_targets_gateway():
    data = yaml.safe_load(PROMETHEUS_YML.read_text())
    jobs = data["scrape_configs"]
    assert any(
        j.get("job_name") == "gateway"
        and "gateway:8000" in j["static_configs"][0]["targets"]
        for j in jobs
    )
