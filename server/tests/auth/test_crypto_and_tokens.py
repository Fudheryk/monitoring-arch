# server/tests/unit/auth/test_crypto_and_tokens.py
import time
import importlib

def test_password_hash_and_verify(monkeypatch):
    # S'assurer que les imports se font après config éventuelle
    from app.core.security import hash_password, verify_password

    h = hash_password("secret123")
    assert h != "secret123"
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)

def test_jwt_create_and_decode(monkeypatch):
    # Fixer le secret AVANT d'importer le module qui le lit
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    # Si ton module lit le secret à l'import, force le reload
    import app.core.security as sec
    importlib.reload(sec)

    token = sec.create_access_token({"sub": "user-id-1"})
    data = sec.decode_token(token)
    assert data["sub"] == "user-id-1"

def test_jwt_expiry(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    import app.core.security as sec
    importlib.reload(sec)

    token = sec.create_access_token({"sub": "u"}, expires_seconds=1)
    time.sleep(2)
    # selon ton implémentation: None ou exception; ici on suppose None
    assert sec.decode_token(token) is None
