"""Guards on how the OIDC path calls PyJWT."""

from __future__ import annotations


def test_no_jwt_decode_can_hit_the_unfixed_options_mutation_bug() -> None:
    """PYSEC-2026-4146 (no fixed PyJWT release as of 2026-10-04): `decode()`
    mutates a caller-supplied `options` dict in place whenever
    `verify_signature` is falsy, so a REUSED options dict carries
    `verify_exp=False` etc. into a later, supposedly full verification —
    expired / wrong-audience / wrong-issuer tokens then pass. We are safe
    only because no decode call passes `options` and nothing disables
    signature verification. This keeps it that way until upstream fixes it."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "src"
    offending: list[str] = []
    for path in src.rglob("*.py"):
        text = path.read_text()
        if "verify_signature" in text:
            offending.append(f"{path.name}: mentions verify_signature")
        for node in ast.walk(ast.parse(text)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("decode", "decode_complete")
                and any(k.arg == "options" for k in node.keywords)
            ):
                offending.append(f"{path.name}:{node.lineno}: decode(..., options=...)")
    assert not offending, offending
