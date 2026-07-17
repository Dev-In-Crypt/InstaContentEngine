"""Credential encryption at rest (services/secrets)."""
import services.secrets as secrets


def test_encrypt_decrypt_round_trip():
    token = secrets.encrypt("sk-or-v1-supersecret")
    assert secrets.decrypt(token) == "sk-or-v1-supersecret"


def test_ciphertext_is_not_plaintext():
    # The whole point: the stored value must not reveal the secret.
    plain = "instagram-token-abc123"
    token = secrets.encrypt(plain)
    assert plain not in token
    assert token != plain


def test_empty_round_trips_to_empty():
    assert secrets.encrypt("") == ""
    assert secrets.decrypt("") == ""


def test_decrypt_tampered_returns_none():
    token = secrets.encrypt("secret")
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    assert secrets.decrypt(tampered) is None


def test_decrypt_garbage_returns_none():
    assert secrets.decrypt("not-a-fernet-token") is None
