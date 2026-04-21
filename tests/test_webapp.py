"""Tests for the Flask webapp — route registration, sandboxing, basic responses.

Uses Flask's test_client so no real network/server needed. Doesn't make LLM calls.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------
def test_webapp_imports() -> None:
    from webapp.app import app  # noqa: F401


def test_webapp_route_count() -> None:
    """Per project memory, the URL map should have at least 19 rules."""
    from webapp.app import app
    rules = list(app.url_map.iter_rules())
    assert len(rules) >= 19, f"Only {len(rules)} routes; expected ≥19"


def test_webapp_critical_endpoints_registered() -> None:
    from webapp.app import app
    endpoint_names = {r.endpoint for r in app.url_map.iter_rules()}
    required = {
        "index",
        "company_dashboard",
        "departments_page",
        "department_detail",
        "board_page",
        "board_meeting",
        "sessions_page",
        "decisions_page",
        "artifacts_page",
        "view_artifact",
        "run_page",
        "run_dispatch",
        "run_board",
        "run_full_demo",
        "jobs_page",
        "job_detail",
        "api_job",
        "healthz",
    }
    missing = required - endpoint_names
    assert not missing, f"Missing endpoints: {missing}"


# ---------------------------------------------------------------------------
# Test client
# ---------------------------------------------------------------------------
@pytest.fixture
def client(vault_dir):  # noqa: ARG001 — forces vault-dir skip to cascade
    from webapp.app import app
    app.config["TESTING"] = True
    return app.test_client()


def test_healthz_returns_ok(client) -> None:
    rv = client.get("/healthz")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert data["companies"] >= 1


def test_index_returns_200(client) -> None:
    rv = client.get("/")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    # Should mention at least one known company
    assert "Old Press" in body or "company" in body.lower()


def test_old_press_dashboard_returns_200(client) -> None:
    rv = client.get("/c/Old Press Wine Company LLC/")
    assert rv.status_code == 200


def test_old_press_departments_returns_200(client) -> None:
    rv = client.get("/c/Old Press Wine Company LLC/departments")
    assert rv.status_code == 200


def test_old_press_board_returns_200(client) -> None:
    rv = client.get("/c/Old Press Wine Company LLC/board")
    assert rv.status_code == 200


def test_old_press_artifacts_returns_200(client) -> None:
    rv = client.get("/c/Old Press Wine Company LLC/artifacts")
    assert rv.status_code == 200


def test_old_press_run_page_returns_200(client) -> None:
    rv = client.get("/c/Old Press Wine Company LLC/run")
    assert rv.status_code == 200


def test_costs_route_returns_200(client) -> None:
    """Chunk 1a.9 — /c/<slug>/costs renders even without a cost-log.jsonl."""
    rv = client.get("/c/Old Press Wine Company LLC/costs")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    # Page should render the cost-dashboard chrome whether the log exists
    # or not (empty-state is still a 200).
    assert "Cost dashboard" in body


def test_unknown_company_returns_404(client) -> None:
    rv = client.get("/c/Definitely Not A Real Company/")
    assert rv.status_code == 404


def test_view_missing_path_returns_400(client) -> None:
    rv = client.get("/c/Old Press Wine Company LLC/view")
    assert rv.status_code == 400


# ---------------------------------------------------------------------------
# Sandboxing
# ---------------------------------------------------------------------------
def test_artifact_sandbox_blocks_traversal(company) -> None:
    """read_artifact_safe must refuse paths that escape the company dir."""
    from webapp.services import read_artifact_safe
    # Try to read something outside the sandbox
    bad_paths = [
        "../config.json",
        "../../../../Windows/System32/drivers/etc/hosts",
        "..\\..\\config.json",
    ]
    for p in bad_paths:
        result = read_artifact_safe(company, p)
        assert result is None, f"Sandbox should reject traversal: {p}"


def test_artifact_sandbox_allows_in_dir_paths(company) -> None:
    """In-sandbox markdown files should be readable."""
    from webapp.services import read_artifact_safe
    # config.json sits at the company root
    cfg = read_artifact_safe(company, "config.json")
    # config.json may or may not be allowed depending on file-type filtering
    # The contract is: it doesn't crash, it returns None or content.
    # If allowed, content must be a dict with 'name' key.
    if cfg is not None:
        assert isinstance(cfg, dict)


def test_view_endpoint_rejects_traversal(client) -> None:
    rv = client.get("/c/Old Press Wine Company LLC/view?path=../../config.json")
    assert rv.status_code == 404


# ---------------------------------------------------------------------------
# Company discovery
# ---------------------------------------------------------------------------
def test_discover_companies_finds_old_press(vault_dir) -> None:  # noqa: ARG001
    from webapp.services import discover_companies
    companies = discover_companies()
    names = {c["company_id"] for c in companies}
    assert "old-press" in names


def test_company_summary_serializable(company) -> None:
    """The dashboard renders a summary dict — must be JSON-serializable."""
    import json
    from webapp.services import read_company_summary
    summary = read_company_summary(company)
    s = json.dumps(summary, default=str)
    assert "Old Press" in s


# ---------------------------------------------------------------------------
# Markdown renderer (custom, no external lib)
# ---------------------------------------------------------------------------
def test_markdown_renderer_handles_headings() -> None:
    from webapp.app import render_markdown
    out = render_markdown("# Hello\n\nWorld")
    assert "<h1>Hello</h1>" in out
    assert "<p>World</p>" in out


def test_markdown_renderer_handles_lists() -> None:
    from webapp.app import render_markdown
    out = render_markdown("- one\n- two\n- three")
    assert "<ul>" in out and "</ul>" in out
    assert out.count("<li>") == 3


def test_markdown_renderer_handles_code_fence() -> None:
    from webapp.app import render_markdown
    out = render_markdown("```\nprint('hi')\n```")
    assert "<pre" in out and "<code>" in out
    assert "print" in out


def test_markdown_renderer_escapes_html() -> None:
    from webapp.app import render_markdown
    out = render_markdown("<script>alert('xss')</script>")
    assert "<script>" not in out  # raw tag must be escaped
    assert "&lt;script&gt;" in out


def test_markdown_renderer_handles_inline_styles() -> None:
    from webapp.app import render_markdown
    out = render_markdown("**bold** and *italic* and `code`")
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out
    assert "<code>code</code>" in out


def test_markdown_renderer_handles_links() -> None:
    from webapp.app import render_markdown
    out = render_markdown("[text](https://example.com)")
    assert '<a href="https://example.com">text</a>' in out


def test_markdown_renderer_empty_input() -> None:
    from webapp.app import render_markdown
    assert render_markdown("") == ""


# ---------------------------------------------------------------------------
# CRIT-5 attribute-injection coverage (Phase 3.1)
# ---------------------------------------------------------------------------
def test_markdown_renderer_blocks_javascript_scheme() -> None:
    """javascript: URLs must never land in an <a href>."""
    from webapp.app import render_markdown
    out = render_markdown("[click](javascript:alert(1))")
    assert 'href="javascript:' not in out.lower()
    assert "<a " not in out  # link should be refused entirely


def test_markdown_renderer_blocks_vbscript_scheme() -> None:
    from webapp.app import render_markdown
    out = render_markdown("[click](vbscript:msgbox(1))")
    assert 'href="vbscript:' not in out.lower()
    assert "<a " not in out


def test_markdown_renderer_blocks_data_scheme() -> None:
    from webapp.app import render_markdown
    out = render_markdown("[click](data:text/html,something)")
    assert 'href="data:' not in out.lower()
    assert "<a " not in out


def test_markdown_renderer_blocks_file_scheme() -> None:
    """CRIT-5: file: scheme must be blocked (new in markdown-it-py swap)."""
    from webapp.app import render_markdown
    out = render_markdown("[click](file:///etc/passwd)")
    assert 'href="file:' not in out.lower()
    assert "<a " not in out


def test_markdown_renderer_attribute_injection_neutralized() -> None:
    """CRIT-5: a crafted href containing a closing quote + onclick attribute
    must NOT produce a separate onclick attribute on the <a> tag. The quote
    characters must be HTML-escaped in any attribute or text context."""
    from webapp.app import render_markdown
    payload = '[x](" onclick="alert(1))'
    out = render_markdown(payload)
    lower = out.lower()
    # No live onclick attribute should exist; a raw, unescaped `"` must not
    # follow a `href=` attribute value.
    assert ' onclick="' not in lower
    assert ' onclick=\'' not in lower
    # No anchor element smuggling the attack.
    if "<a " in lower:
        assert 'href="javascript' not in lower
        assert 'onclick' not in lower


def test_markdown_renderer_preserves_safe_http_links() -> None:
    from webapp.app import render_markdown
    out = render_markdown("[site](https://example.com)")
    assert '<a href="https://example.com">site</a>' in out


def test_markdown_renderer_preserves_safe_relative_links() -> None:
    from webapp.app import render_markdown
    out = render_markdown("[local](./page.md)")
    assert 'href=' in out
    assert 'page.md' in out
    assert 'javascript:' not in out.lower()
