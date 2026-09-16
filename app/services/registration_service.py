"""
AIM Data Device Registration Service
======================================

PURPOSE:
    Registers this AIM Data instance with ai.market by sending
    Ed25519 + X25519 public keys, and stores the returned platform
    public keys + certificate locally for Trust Channel handshake.

BQ-102 ST-3: Registration client
- POST /api/v1/trust/register with key_type="ed25519"
- Exponential backoff retry (max 3 attempts, AG Council review)
- 409 recovery: re-fetch platform keys for already-registered devices
- Atomic keystore storage via DeviceCrypto
- Non-blocking: logs warning on failure, app continues
"""

import hashlib
import logging
import platform
from typing import Optional

import httpx

from app.config import settings
from app.core.crypto import DeviceCrypto

logger = logging.getLogger(__name__)

# Retry config
MAX_RETRIES = 3
INITIAL_BACKOFF_S = 2.0  # doubles each retry


def _get_device_id() -> str:
    """
    Generate a deterministic device ID from machine characteristics.
    Uses platform node (MAC-based) + machine type as a stable fingerprint.
    In Docker, this should be stable across container restarts if the
    hostname/MAC is preserved.
    """
    raw = f"{platform.node()}:{platform.machine()}:{platform.system()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_os_type() -> str:
    """Map platform.system() to OSType enum values."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    elif system == "linux":
        # Check if running in Docker
        try:
            with open("/proc/1/cgroup", "r") as f:
                if "docker" in f.read():
                    return "docker"
        except (FileNotFoundError, PermissionError):
            pass
        return "linux"
    elif system == "windows":
        return "windows"
    return "linux"


async def register_with_marketplace(
    crypto: DeviceCrypto,
    api_key: Optional[str] = None,
    access_token: Optional[str] = None,
    max_retries: int = MAX_RETRIES,
) -> bool:
    """
    Register this device with ai.market's Trust Channel.

    Sends Ed25519 + X25519 public keys to POST /api/v1/trust/register
    and stores the returned platform keys + certificate.

    Args:
        crypto: Initialized DeviceCrypto with keypairs generated.
        api_key: API key for authentication. If None, uses internal_api_key.
        access_token: ai.market bearer token; takes precedence over API keys.
        max_retries: Maximum attempts; login uses one, startup defaults to three.

    Returns:
        True if registration succeeded or device already registered with keys.
        False if registration failed (non-fatal).
    """
    registered, _auth_status = await _register_with_marketplace(
        crypto, api_key=api_key, access_token=access_token, max_retries=max_retries,
    )
    return registered


async def _register_with_marketplace(
    crypto: DeviceCrypto,
    api_key: Optional[str] = None,
    access_token: Optional[str] = None,
    max_retries: int = MAX_RETRIES,
) -> tuple[bool, Optional[int]]:
    """Return registration success and an auth refusal status for credential recovery."""
    # Skip if already have platform keys
    if crypto.has_platform_keys():
        logger.info("Platform keys already present in keystore — skipping registration")
        return True, None

    ed25519_pub_b64, x25519_pub_b64 = crypto.get_public_keys_b64()
    device_id = _get_device_id()
    os_type = _get_os_type()

    payload = {
        "device_id": device_id,
        "vectoraiz_version": "2.0.0",  # TODO: read from package
        "os_type": os_type,
        "key_type": "ed25519",
        "ed25519_public_key": ed25519_pub_b64,
        "x25519_public_key": x25519_pub_b64,
    }

    auth_key = api_key or settings.internal_api_key
    if not access_token and not auth_key:
        logger.warning("No credential available for device registration — skipping")
        return False, None

    headers = {
        **({"Authorization": f"Bearer {access_token}"} if access_token else {"X-API-Key": auth_key}),
        "Content-Type": "application/json",
    }

    url = f"{settings.ai_market_url}/api/v1/trust/register"
    backoff = INITIAL_BACKOFF_S

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                crypto.store_platform_keys(
                    platform_ed25519_pub=data.get("ai_market_ed25519_public_key", ""),
                    platform_x25519_pub=data.get("ai_market_x25519_public_key", ""),
                    certificate=data.get("certificate", ""),
                )
                logger.info(f"Device registered with ai.market (device_id={device_id[:16]}...)")
                return True, None

            elif resp.status_code == 409:
                # AG Council: handle "already registered" — response may include platform keys
                data = resp.json()
                if data.get("ai_market_ed25519_public_key"):
                    # 409 with platform keys = already registered, just store them
                    crypto.store_platform_keys(
                        platform_ed25519_pub=data.get("ai_market_ed25519_public_key", ""),
                        platform_x25519_pub=data.get("ai_market_x25519_public_key", ""),
                        certificate=data.get("certificate", ""),
                    )
                    logger.info("Device already registered — platform keys recovered from 409")
                    return True, None
                else:
                    logger.warning("Device already registered but no platform keys in response")
                    return False, None

            elif resp.status_code in (401, 403):
                logger.error(f"Registration auth failed ({resp.status_code})")
                return False, resp.status_code  # Don't retry auth failures

            else:
                logger.warning(
                    f"Registration attempt {attempt}/{max_retries} failed: "
                    f"{resp.status_code}"
                )

        except httpx.TimeoutException:
            logger.warning(f"Registration attempt {attempt}/{max_retries} timed out")
        except httpx.ConnectError:
            logger.warning(f"Registration attempt {attempt}/{max_retries} connection error")
        except Exception:
            logger.error(f"Registration attempt {attempt}/{max_retries} unexpected error")

        # Exponential backoff (skip sleep on last attempt)
        if attempt < max_retries:
            import asyncio
            logger.info(f"Retrying in {backoff:.0f}s...")
            await asyncio.sleep(backoff)
            backoff *= 2

    logger.error(f"Device registration failed after {max_retries} attempts — registration remains incomplete")
    return False, None


async def ensure_trust_device_registered(
    crypto: DeviceCrypto,
    access_token: Optional[str] = None,
    max_retries: int = MAX_RETRIES,
) -> bool:
    """Ensure Trust registration: explicit token > internal API key > stored token.

    API-key installs are unchanged and never send a stored bearer first. If a
    stored token is refused with 401/403 and an internal key has become configured,
    recover with one key attempt, without retrying the refused credential.
    OAuth-only installs with an expired stored token self-heal on the next token
    refresh or sign-in: both pass through complete_connected_login with a fresh
    token. No restart is needed; the client re-reads the keystore on every reconnect.
    Startup defaults to three attempts; login requests one to avoid retry backoff.
    """
    if crypto.has_platform_keys():
        return True

    if access_token or settings.internal_api_key:
        return await register_with_marketplace(
            crypto, access_token=access_token, max_retries=max_retries,
        )

    from app.services.serial_store import get_serial_store

    stored_token = get_serial_store().state.ai_market_access_token
    registered, auth_status = await _register_with_marketplace(
        crypto, access_token=stored_token, max_retries=max_retries,
    )
    if stored_token and auth_status in (401, 403) and settings.internal_api_key:
        return await register_with_marketplace(
            crypto, max_retries=1,
        )
    return registered


async def ensure_vz_install_registered(
    crypto: DeviceCrypto,
    access_token: Optional[str] = None,
    seller_id: Optional[str] = None,
) -> Optional[str]:
    """
    Register this install for signed VZ publish, returning ai.market install_id.

    This is separate from the Trust Channel registration above. ai.market binds
    the Ed25519 public key to the authenticated seller and returns the UUID that
    must be used as JWT iss for /api/v1/vz/publish.
    """
    from app.services.serial_store import ACTIVE, get_serial_store

    store = get_serial_store()
    serial_bound = bool(
        store.state.state == ACTIVE
        and store.state.serial
        and store.state.install_token
    )
    if store.state.vz_install_id:
        if serial_bound and not store.state.vz_install_serial_bound:
            # S1717: an install registered before serial binding existed keeps
            # its cached install_id but must re-register once with the activated
            # serial so ai.market binds the serial to THIS install. Fall through
            # to the registration request below instead of giving up.
            logger.info(
                "Cached VZ install is unbound despite activated serial credentials; "
                "re-registering to bind the serial (install_id=%s)",
                store.state.vz_install_id,
            )
        else:
            return store.state.vz_install_id

    token = access_token or store.state.ai_market_access_token
    if not token:
        logger.warning("No ai.market bearer token available for VZ install registration")
        return None

    ed25519_pub_b64, _x25519_pub_b64 = crypto.get_public_keys_b64()
    url = f"{settings.ai_market_url}/api/v1/vz/register"
    payload = {"public_key_b64": ed25519_pub_b64}
    if serial_bound:
        payload["serial"] = store.state.serial
        payload["serial_install_token"] = store.state.install_token

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.TimeoutException:
        logger.warning("VZ install registration timed out")
        return None
    except httpx.RequestError as exc:
        logger.warning("VZ install registration request failed: %s", exc)
        return None

    cached_install_id = store.state.vz_install_id

    if resp.status_code in (200, 201):
        data = resp.json()
        install_id = str(data.get("install_id") or "")
        install_token = data.get("install_token")
        if install_id and cached_install_id and install_id != cached_install_id:
            # S1717: a re-register must keep THIS install's identity; a different
            # id would detach published listings. Leave the cache untouched.
            logger.warning(
                "VZ install re-register returned a different install_id; keeping the cached install unbound"
            )
            return None
        if install_id:
            store.persist_vz_install(install_id, install_token, serial_bound=serial_bound)
            if seller_id:
                store.state.ai_market_seller_id = seller_id
                store.save()
            logger.info("VZ install registered with ai.market: install_id=%s", install_id)
            return install_id
        logger.warning("VZ install registration succeeded without install_id: %s", data)
        return None

    if resp.status_code == 409:
        data = resp.json()
        install_id = str(data.get("install_id") or "")
        install_token = data.get("install_token")
        if install_id and cached_install_id and install_id != cached_install_id:
            logger.warning(
                "VZ install re-register returned a different install_id; keeping the cached install unbound"
            )
            return None
        if install_id:
            store.persist_vz_install(install_id, install_token, serial_bound=serial_bound)
            logger.info("VZ install already registered: install_id=%s", install_id)
            return install_id
        if cached_install_id and serial_bound and not store.state.vz_install_serial_bound:
            # S1717 re-register path: a 409 without an install id proves nothing
            # about the binding; fail this call instead of looping on a cached,
            # still-unbound id.
            logger.warning("VZ install re-register conflict without install_id; binding not confirmed")
            return None
        logger.info("VZ install already registered, but ai.market did not return install_id")
        return store.state.vz_install_id

    if resp.status_code in (401, 403):
        logger.warning("VZ install registration auth failed (%d): %s", resp.status_code, resp.text[:300])
        return None

    logger.warning("VZ install registration failed (%d): %s", resp.status_code, resp.text[:300])
    return None


def read_preview_registration_evidence(path):
    """Read locally supplied owner-bound evidence, never infer it from install ID.

    Existing /register and /rotate-key return IDs only. A fresh registry readback
    must be supplied by the owner-authorized evidence flow; no new endpoint here.
    """
    import json
    from pathlib import Path
    from app.services.preview_signing_service import RegistrationEvidence, closed, SigningError
    try:
        target = Path(path)
        if target.is_symlink() or target.stat().st_size > 4096:
            raise ValueError
        return RegistrationEvidence(**closed(RegistrationEvidence, json.loads(target.read_bytes())))
    except Exception:
        raise SigningError("registration_evidence_unavailable") from None


async def rotate_preview_install_key(crypto, *, install_id, access_token):
    """Stage encrypted Ed25519 key; HTTP success is NOT key readback confirmation.

    Pending sentinel survives crashes/timeouts and blocks signing. Caller must
    reconcile with owner-bound evidence before invoking activation below.
    """
    import os
    from app.services.preview_signing_service import SigningError
    pending = crypto.keystore_path.with_suffix(".rotation-pending")
    staged = crypto.keystore_path.with_suffix(".rotation-staged")
    try:
        if not access_token or pending.exists() or staged.exists():
            raise ValueError
        old = crypto._read_keystore()
        crypto._load_keys(old)  # Validate old identity; never generate on loss.
        fd = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.fsync(fd)
        os.close(fd)
        new_priv, new_pub = crypto.generate_ed25519_keypair()
        encrypted, salt = crypto._encrypt_private_key(new_priv)
        from app.services.preview_signing_service import public_bytes
        import base64
        replacement = dict(old)
        replacement.update(ed25519_public_key=public_bytes(new_pub).hex(),
                           encrypted_ed25519_private_key=encrypted.decode("latin-1"), ed25519_salt=salt.hex())
        stage_crypto = DeviceCrypto(str(staged), crypto._passphrase.decode())
        stage_crypto._write_keystore(replacement)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.ai_market_url}/api/v1/vz/rotate-key",
                json={"install_id": install_id, "new_public_key_b64": base64.b64encode(public_bytes(new_pub)).decode()},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code != 200 or response.json().get("install_id") != install_id:
            raise ValueError
        return "rotation_pending"  # Even success needs independent key readback.
    except Exception:
        raise SigningError("rotation_pending") from None


def activate_preview_install_rotation(crypto, *, evidence, install_id, seller_id, now, max_age):
    import os
    from app.services.preview_signing_service import check_evidence, public_bytes, SigningError
    staged = crypto.keystore_path.with_suffix(".rotation-staged")
    pending = crypto.keystore_path.with_suffix(".rotation-pending")
    try:
        if not pending.exists() or staged.is_symlink():
            raise ValueError
        stage = DeviceCrypto(str(staged), crypto._passphrase.decode())
        keys = stage._load_keys(stage._read_keystore())
        check_evidence(evidence, install_id=install_id, seller_id=seller_id,
                       raw_key=public_bytes(keys[1]), now=now, max_age=max_age)
        os.replace(staged, crypto.keystore_path)
        pending.unlink()
    except Exception:
        raise SigningError("rotation_pending") from None


async def revoke_preview_install(crypto, *, install_id, access_token):
    import os
    from app.services.preview_signing_service import SigningError
    pending = crypto.keystore_path.with_suffix(".rotation-pending")
    # Revocation uncertainty must prevent any additional local signatures.
    fd = os.open(pending, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        if not access_token:
            raise ValueError
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(
                f"{settings.ai_market_url}/api/v1/vz/install/{install_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code != 204:
            raise ValueError
        return "revoked"
    except Exception:
        raise SigningError("revocation_pending") from None
