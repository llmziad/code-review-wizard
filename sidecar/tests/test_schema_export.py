"""JSON Schema export — verifies the IPC contract surface for the Electron app.

If this test ever flags a *removal* of a public type, that's a breaking change
to the desktop app's TypeScript bindings — bump a version, don't silently drop.
"""

from __future__ import annotations

from reviewer.schema_export import build_schema_doc

# Every public IPC type that the desktop app's renderer is allowed to consume.
# Adding to this set is fine; removing requires bumping the contract version.
EXPECTED_PUBLIC_TYPES = {
    "PRSummary",
    "ContextBundle",
    "ReviewReport",
    "PipelineEvent",
    "StoredAuth",
    "DeviceFlowChallenge",
    # Transitively pulled by the above:
    "PRMetadata",
    "ChangedSymbol",
    "SymbolContext",
    "CallSite",
    "ReviewComment",
    "CostSummary",
}


def test_schema_doc_has_dollar_defs_and_metadata() -> None:
    doc = build_schema_doc()
    assert "$defs" in doc
    assert "title" in doc
    assert "description" in doc


def test_schema_doc_contains_every_expected_public_type() -> None:
    doc = build_schema_doc()
    defs = set(doc["$defs"].keys())
    missing = EXPECTED_PUBLIC_TYPES - defs
    assert not missing, f"Public types missing from schema export: {missing}"


def test_review_comment_schema_includes_id_field() -> None:
    """The id field is what the desktop app uses for triage (approve/dismiss/edit)."""
    doc = build_schema_doc()
    rc = doc["$defs"]["ReviewComment"]
    assert "id" in rc["properties"]


def test_pipeline_event_schema_includes_stage_and_event_fields() -> None:
    """The renderer's progress UI keys off these fields."""
    doc = build_schema_doc()
    pe = doc["$defs"]["PipelineEvent"]
    assert "stage" in pe["properties"]
    assert "event" in pe["properties"]
