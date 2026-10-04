import pytest

from app.auth.security import Principal, decode_token, hash_password, issue_token, verify_password


def test_password_hashing_is_salted_and_verifiable():
    password = "unit-test-password-long"
    first = hash_password(password)
    second = hash_password(password)
    assert first.startswith("scrypt$")
    assert first != second
    assert verify_password(password, first)
    assert not verify_password("wrong-password", first)


def test_short_password_is_rejected():
    with pytest.raises(ValueError, match="12 characters"):
        hash_password("short")


def test_signed_bearer_token_round_trip_and_expiry():
    principal = Principal(user_id="u1", username="researcher", role="user")
    token = issue_token(principal, "x" * 48, now=1000)
    assert decode_token(token, "x" * 48, now=1001) == principal
    with pytest.raises(ValueError, match="Invalid or expired"):
        decode_token(token, "y" * 48, now=1001)
    with pytest.raises(ValueError, match="Invalid or expired"):
        decode_token(token, "x" * 48, now=3000)


def test_token_issuer_rejects_short_secret():
    with pytest.raises(ValueError, match="32 characters"):
        issue_token(Principal(user_id="u", username="u", role="admin"), "weak")
