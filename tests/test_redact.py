import pytest

from twice.redact import redact


@pytest.mark.parametrize(
    "secret, kind",
    [
        ("sk-ant-api03-abcdefghijklmnop", "anthropic_key"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123", "github_token"),
        ("AKIAABCDEFGHIJKLMNOP", "aws_key"),
        ("someone@example.com", "email"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmno", "jwt"),
    ],
)
def test_known_secrets(secret, kind):
    out = redact(f"value: {secret} end")
    assert secret not in out and f"[REDACTED:{kind}]" in out


def test_assignments_keep_the_key_name():
    assert redact("password=hunter2 and token: abc123") == "password=[REDACTED] and token: [REDACTED]"


def test_private_key_block():
    block = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    assert redact(block) == "[REDACTED:private_key]"


def test_plain_text_untouched():
    text = "Never use em dashes in UI copy."
    assert redact(text) == text
