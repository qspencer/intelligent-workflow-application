"""CODIFY_PLAN §6 trusted-header authentication policy."""

from __future__ import annotations

from workflow_platform.connectors.email.auth_results import authentication_pass

GMAIL_DMARC = (
    "mx.google.com; dkim=pass header.i=@nytimes.com header.s=x; "
    "spf=pass smtp.mailfrom=nytimes.com; dmarc=pass (p=REJECT) header.from=nytimes.com"
)
GMAIL_DKIM_ONLY = "mx.google.com; dkim=pass header.i=@nytimes.com header.s=x; spf=softfail"
GMAIL_FAIL = "mx.google.com; dkim=fail header.i=@nytimes.com; dmarc=fail header.from=nytimes.com"
ATTACKER = "mx.google.com; dmarc=pass header.from=nytimes.com"  # appended below Gmail's


def test_dmarc_pass_suffices() -> None:
    assert authentication_pass([GMAIL_DMARC], "news@nytimes.com")


def test_aligned_dkim_accepted_without_dmarc() -> None:
    assert authentication_pass([GMAIL_DKIM_ONLY], "news@nytimes.com")


def test_misaligned_dkim_rejected() -> None:
    assert not authentication_pass([GMAIL_DKIM_ONLY], "news@evil.com")


def test_fail_verdict_rejected() -> None:
    assert not authentication_pass([GMAIL_FAIL], "news@nytimes.com")


def test_absent_and_malformed_fail_closed() -> None:
    assert not authentication_pass([], "a@b.com")
    assert not authentication_pass(["garbage ;;; ="], "a@b.com")
    assert not authentication_pass([";"], "a@b.com")


def test_untrusted_authserv_ignored() -> None:
    assert not authentication_pass(
        ["relay.evil.com; dmarc=pass header.from=nytimes.com"], "news@nytimes.com"
    )


def test_first_trusted_entry_decides_attacker_appended_ignored() -> None:
    # Gmail's genuine verdict (fail) is topmost; an attacker-appended
    # trusted-looking pass BELOW must not resurrect it.
    assert not authentication_pass([GMAIL_FAIL, ATTACKER], "news@nytimes.com")


def test_idn_domains_compared_punycode() -> None:
    ar = "mx.google.com; dkim=pass header.d=bücher.example"
    assert authentication_pass([ar], "a@xn--bcher-kva.example")
