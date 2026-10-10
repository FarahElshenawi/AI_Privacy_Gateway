"""Where the vault's secrets live.

Order of preference for each secret:
  1. the operating system's credential store (Windows Credential Locker / DPAPI, macOS Keychain,
     Linux Secret Service) through the optional `keyring` package;
  2. an owner-only (0600) file in the user's home directory, the previous behaviour.

`DLP_KEYSTORE=file` skips the OS store (headless servers, tests). A secret found only in the old
file is moved into the OS store when one is available and read back correctly; the file is then
removed so the key no longer sits on disk next to the data it protects.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("dlp.keystore")
SERVICE = "doppel-ai-privacy-gateway"


def _keyring():
    if os.environ.get("DLP_KEYSTORE", "auto").lower() == "file":
        return None
    try:
        import keyring
        from keyring.backends import fail
        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:  # noqa: BLE001 - not installed / no backend: file fallback
        return None


def _file(name: str) -> Path:
    return Path.home() / f".pii_gateway_{name}.key"


def get_secret(name: str) -> Optional[bytes]:
    kr = _keyring()
    if kr is not None:
        try:
            v = kr.get_password(SERVICE, name)
            if v:
                return v.encode("ascii")
        except Exception as exc:  # noqa: BLE001
            log.warning("OS keystore read failed (%s); using file", type(exc).__name__)
    f = _file(name)
    try:
        data = f.read_bytes().strip() if f.exists() else b""
    except OSError:
        return None
    if not data:
        return None
    if kr is not None and set_secret(name, data, _migrating=True):
        try:
            f.unlink()
        except OSError:
            pass
    return data


def set_secret(name: str, value: bytes, *, _migrating: bool = False) -> bool:
    """Store a secret. True if it went to the OS store; otherwise it is written to the 0600 file
    and False is returned."""
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(SERVICE, name, value.decode("ascii"))
            if kr.get_password(SERVICE, name) == value.decode("ascii"):
                return True
        except Exception as exc:  # noqa: BLE001
            log.warning("OS keystore write failed (%s)", type(exc).__name__)
    if _migrating:
        return False
    f = _file(name)
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(f), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, value)
    finally:
        os.close(fd)
    return False
