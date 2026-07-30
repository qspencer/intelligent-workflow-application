"""EMAIL_TRIAGE_CODIFY_PLAN v2 §2 eligibility rule (pure logic)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from workflow_platform.codify import (
    Eligibility,
    SenderFacts,
    VerdictRow,
    corrected_domain_set,
    evaluate_sender,
    registrable_domain,
)

NOW = datetime(2026, 7, 30, tzinfo=UTC)
OWNER = "qspencer@gmail.com"


def _rows(
    sender: str,
    n: int,
    *,
    category: str = "newsletter",
    schema: int = 2,
    attention: list[str] | None = None,
    source: str = "classifier",
    span_hours: float = 72.0,
) -> list[VerdictRow]:
    return [
        VerdictRow(
            sender=sender,
            message_id=f"m{i}",
            category=category,
            attention=attention or [],
            schema_version=schema,
            decision_source=source,
            at=NOW - timedelta(hours=span_hours) + timedelta(hours=i * (span_hours / max(n, 1))),
        )
        for i in range(n)
    ]


def _eval(
    sender: str, rows: list[VerdictRow], facts: SenderFacts | None = None, **kw: object
) -> Eligibility:
    return evaluate_sender(
        sender,
        rows,
        facts or SenderFacts(),
        mailbox_owner=OWNER,
        corrected_domains=kw.pop("corrected_domains", frozenset()),  # type: ignore[arg-type]
        now=NOW,
        **kw,  # type: ignore[arg-type]
    )


def test_promotion_at_threshold() -> None:
    out = _eval("news@example.com", _rows("news@example.com", 5))
    assert out.eligible and out.category == "newsletter"
    assert out.current_schema_messages == 5


def test_below_floor_and_legacy_cannot_substitute() -> None:
    rows = _rows("news@example.com", 4) + _rows("news@example.com", 10, schema=1)
    out = _eval("news@example.com", rows, SenderFacts(legacy_confirmed=10))
    assert not out.eligible
    assert any("current-schema floor" in r for r in out.reasons)


def test_replays_collapse_to_distinct_messages() -> None:
    rows = _rows("news@example.com", 5)
    rows += [
        VerdictRow(**{**vars(rows[0]), "at": rows[0].at + timedelta(hours=1)}) for _ in range(6)
    ]
    out = _eval("news@example.com", rows)
    assert out.eligible
    assert out.distinct_messages == 5
    assert out.verdict_events_seen == 11


def test_unanimity_and_attention_clean_required() -> None:
    rows = _rows("news@example.com", 5)
    rows[2].category = "promotion"
    assert not _eval("news@example.com", rows).eligible

    rows = _rows("news@example.com", 5)
    rows[4].attention = ["review"]
    out = _eval("news@example.com", rows)
    assert any("attention-bearing" in r for r in out.reasons)


def test_category_allowlist_first_release() -> None:
    for category, ok in (("notification", True), ("personal", False), ("spam", False)):
        out = _eval("s@example.com", _rows("s@example.com", 5, category=category))
        assert out.eligible is ok, category


def test_single_batch_fails_diversity() -> None:
    out = _eval("s@example.com", _rows("s@example.com", 5, span_hours=2))
    assert any("single-batch" in r for r in out.reasons)


def test_entity_shape_and_owner_excluded() -> None:
    assert not _eval("user|person:x@y.com", _rows("user|person:x@y.com", 5)).eligible
    assert not _eval("org:Acme", _rows("org:Acme", 5)).eligible
    assert not _eval(OWNER, _rows(OWNER, 5)).eligible


def test_corrections_disqualify() -> None:
    out = _eval("s@example.com", _rows("s@example.com", 9), SenderFacts(corrected=1))
    assert not out.eligible


def test_codified_rows_never_count_as_evidence() -> None:
    rows = _rows("s@example.com", 3)
    codified = _rows("s@example.com", 9, source="codified_sender_rule")
    for i, row in enumerate(codified):
        row.message_id = f"c{i}"
    rows += codified
    out = _eval("s@example.com", rows)
    assert out.distinct_messages == 3  # rule applications excluded
    assert not out.eligible


def test_domain_level_disqualification_with_freemail_exemption() -> None:
    corrected = corrected_domain_set(["info@truetradinggroup.com", "bad@gmail.com"])
    assert "truetradinggroup.com" in corrected
    assert "gmail.com" not in corrected  # freemail exempt

    # Sibling address at the same registrable domain: disqualified.
    out = _eval(
        "news@truetradinggroup.com",
        _rows("news@truetradinggroup.com", 11),
        corrected_domains=corrected,
    )
    assert not out.eligible
    assert any("domain-level" in r for r in out.reasons)

    # A gmail.com sender is untouched by another gmail.com correction.
    out = _eval(
        "kate@gmail.com",
        _rows("kate@gmail.com", 5, category="notification"),
        corrected_domains=corrected,
    )
    assert out.eligible


def test_registrable_domain() -> None:
    assert registrable_domain("a@news.weather.com") == "weather.com"
    assert registrable_domain("a@mail.example.co.uk") == "example.co.uk"
