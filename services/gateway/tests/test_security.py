import asyncio

import pytest

from app.security import CredentialCipher, generate_api_key, hash_password, hash_token, verify_password
from app.ssrf import UnsafeUrlError, validate_upstream_url
from app.upstreams import describe_error, exposed_name


def test_password_hashing():
    h = hash_password("s3cret-pass")
    assert h.startswith("scrypt$") and "s3cret" not in h
    assert verify_password("s3cret-pass", h) and not verify_password("wrong", h)


def test_api_key_format():
    key, prefix, digest = generate_api_key()
    assert key.startswith("mgw_") and key.startswith(prefix) and digest == hash_token(key) and len(digest) == 64


def test_cipher_rotation():
    old = CredentialCipher("old-key")
    token = old.encrypt("upstream-secret")
    assert "upstream-secret" not in token
    rotated = CredentialCipher("new-key,old-key")      # new key first, old still decrypts
    assert rotated.decrypt(token) == "upstream-secret"
    assert CredentialCipher("new-key").decrypt(rotated.rotate(token)) == "upstream-secret"


def test_exposed_names():
    assert exposed_name("woocommerce", "list_orders") == "woocommerce__list_orders"
    assert exposed_name("x", "weird.tool/name") == "x__weird_tool_name"
    assert len(exposed_name("s", "t" * 100)) <= 64


def test_ssrf_allows_internal_pattern_and_public():
    asyncio.run(validate_upstream_url("http://woo-mcp.urumi.svc.cluster.local:8080/mcp", ["*.urumi.svc.cluster.local"], True))
    asyncio.run(validate_upstream_url("https://8.8.8.8/mcp", [], True))
    with pytest.raises(UnsafeUrlError):
        asyncio.run(validate_upstream_url("http://other.kube-system.svc.cluster.local/mcp", ["*.urumi.svc.cluster.local"], True))


def test_describe_error_unwraps_groups():
    import httpx
    err = ExceptionGroup("g", [httpx.ConnectError("refused")])
    assert "unreachable" in describe_error(err, "woo")
