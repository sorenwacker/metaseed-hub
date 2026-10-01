"""The tests a push runs before it leaves the machine.

A push to a branch runs the hub's structural gates: the tests that scan the
source, the templates and the configuration for a rule (one save path, one
loader, no forked library code, honest names, the CSP, ...). They need no
database and take about fifteen seconds. The pull request's CI runs the whole
suite against Postgres; paying for the full five-minute run twice per push
made people reach for ``--no-verify``, which disables every hook (metaseed
#277, same shape here).

A push of a release tag runs the full suite, because the deploy timer does not
wait for the tag's CI. ``METASEED_PUSH_FULL=1`` asks for the full suite on any
push. pre-commit names the ref being pushed in ``PRE_COMMIT_REMOTE_BRANCH``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping

#: Gate tests: they read files, not the database, so they run anywhere in
#: seconds. ``tests/test_pre_push_runs_the_gates_not_the_suite.py`` checks that
#: each exists and none uses the database session fixture.
GATES = (
    "tests/test_admin_access.py",
    "tests/test_changelog.py",
    "tests/test_collaborators_are_injected.py",
    "tests/test_container_updates.py",
    "tests/test_cookies_are_written_consistently.py",
    "tests/test_deploy_config.py",
    "tests/test_dev_startup_gate.py",
    "tests/test_docs_ai_disclosure.py",
    "tests/test_download_headers_are_not_user_shaped.py",
    "tests/test_entity_form_isolation.py",
    "tests/test_file_size.py",
    "tests/test_footer_shows_both_repos.py",
    "tests/test_home_overview.py",
    "tests/test_htmx_confirmations.py",
    "tests/test_import_order_places_parents_first.py",
    "tests/test_inline_table_headings_carry_the_field_note.py",
    "tests/test_keycloak_realm.py",
    "tests/test_legal_pages_are_reachable.py",
    "tests/test_license_is_stated_once.py",
    "tests/test_metaseed_coupling.py",
    "tests/test_migrations.py",
    "tests/test_mobile_layout.py",
    "tests/test_names_are_honest.py",
    "tests/test_nav_labels.py",
    "tests/test_no_forked_code.py",
    "tests/test_no_forked_templates.py",
    "tests/test_one_clock.py",
    "tests/test_one_draft_state_loader.py",
    "tests/test_one_oidc_discovery.py",
    "tests/test_one_parent_reference_rule.py",
    "tests/test_one_render_path.py",
    "tests/test_pattern_attributes_compile.py",
    "tests/test_privacy_matches_behaviour.py",
    "tests/test_reference_lookups.py",
    "tests/test_renovate_config.py",
    "tests/test_seo_and_privacy.py",
    "tests/test_spec_builder_assets.py",
    "tests/test_stylesheet.py",
    "tests/test_templates_need_no_eval.py",
    "tests/test_term_sources_are_adapters.py",
    "tests/test_the_csp_matches_production.py",
    "tests/test_the_explore_controls_come_first.py",
    "tests/test_the_explorer_draws_what_the_server_sends.py",
    "tests/test_the_explorer_scripts_are_cache_busted.py",
    "tests/test_the_grant_form_is_readable.py",
    "tests/test_the_standards_picker_says_where_to_ask.py",
    "tests/test_the_stylesheet_is_linked_once.py",
    "tests/test_the_stylesheet_is_well_formed.py",
    "tests/test_user_text_never_reaches_markup.py",
    "tests/test_vocabulary.py",
    "tests/test_writes_are_permissive.py",
)
MARKERS = "not selenium"
FULL_SUITE = "full suite"
GATES_ONLY = "structural gates"


def scope(env: Mapping[str, str]) -> str:
    """Which tests this push runs, from the environment pre-commit provides.

    Args:
        env: The process environment.

    Returns:
        ``FULL_SUITE`` for a tag push or an explicit request, ``GATES_ONLY``
        otherwise.
    """
    if env.get("METASEED_PUSH_FULL", "").strip() in {"1", "true", "yes"}:
        return FULL_SUITE
    if env.get("PRE_COMMIT_REMOTE_BRANCH", "").startswith("refs/tags/"):
        return FULL_SUITE
    return GATES_ONLY


def command(which: str) -> list[str]:
    """The pytest command for a scope."""
    base = [sys.executable, "-m", "pytest", "-x", "-q", "--tb=short", "-m", MARKERS]
    return base if which == FULL_SUITE else [*base, *GATES]


def main() -> int:
    """Run the tests for this push and return pytest's exit code."""
    which = scope(os.environ)
    print(f"pre-push: running the {which} (METASEED_PUSH_FULL=1 runs everything)")
    return subprocess.call(command(which))  # noqa: S603 - our own pytest command


if __name__ == "__main__":
    raise SystemExit(main())
