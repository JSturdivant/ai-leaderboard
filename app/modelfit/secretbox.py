"""Authenticated encryption for stored credentials (customers' provider API keys), standard library only.

Encrypt-then-MAC with keys derived from MODELFIT_SECRET_KEY:
  keystream = HMAC-SHA256(k_enc, nonce || counter) blocks (a PRF in counter mode), ciphertext = plaintext XOR keystream
  tag       = HMAC-SHA256(k_mac, "v1" || nonce || ciphertext)
Rotating MODELFIT_SECRET_KEY makes stored keys unreadable (customers re-enter them). At scale, move these to a KMS.
"""

import base64
import hashlib
import hmac
import secrets

from . import settings


def _keys():
    root = settings.get().secret_key.encode()
    return (hmac.new(root, b"modelfit/secretbox/enc", hashlib.sha256).digest(),
            hmac.new(root, b"modelfit/secretbox/mac", hashlib.sha256).digest())


def _stream(key, nonce, n):
    out, i = b"", 0
    while len(out) < n:
        out += hmac.new(key, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
        i += 1
    return out[:n]


def seal(plaintext):
    k_enc, k_mac = _keys()
    nonce = secrets.token_bytes(16)
    pt = plaintext.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(pt, _stream(k_enc, nonce, len(pt))))
    tag = hmac.new(k_mac, b"v1" + nonce + ct, hashlib.sha256).digest()
    return "v1." + base64.urlsafe_b64encode(nonce + tag + ct).decode()


def open_(sealed):
    """The plaintext, or None if the value was tampered with or sealed under another secret key."""
    try:
        ver, b64 = sealed.split(".", 1)
        raw = base64.urlsafe_b64decode(b64)
    except (ValueError, AttributeError):
        return None
    if ver != "v1" or len(raw) < 48:
        return None
    nonce, tag, ct = raw[:16], raw[16:48], raw[48:]
    k_enc, k_mac = _keys()
    if not hmac.compare_digest(tag, hmac.new(k_mac, b"v1" + nonce + ct, hashlib.sha256).digest()):
        return None
    return bytes(a ^ b for a, b in zip(ct, _stream(k_enc, nonce, len(ct)))).decode("utf-8")
