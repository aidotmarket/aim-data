"""Ed25519 Trust Channel v1 wire primitives (backend contract at 58a04603)."""

import base64
import json
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


LIMIT = 1 << 63


def decode_b64(value: str, length: int | None = None) -> bytes:
    if not isinstance(value, str):
        raise ValueError("Missing or invalid base64 field")
    raw = base64.b64decode(value, validate=True)
    if base64.b64encode(raw).decode("ascii") != value:
        raise ValueError("Noncanonical base64 field")
    if length is not None and len(raw) != length:
        raise ValueError(f"Expected {length}-byte field")
    return raw


def handshake_transcript(device_id, ed_public, x_public, client_nonce, server_nonce, ephemeral):
    fields = (device_id.encode("utf-8"), ed_public, x_public, client_nonce, server_nonce, ephemeral)
    if any(len(value) > 65535 for value in fields):
        raise ValueError("Transcript field too long")
    return b"ai.market trust channel v1 handshake" + b"".join(
        struct.pack(">H", len(value)) + value for value in fields
    )


def derive_keys(private_key, ephemeral, client_nonce, server_nonce):
    shared = bytearray(private_key.exchange(x25519.X25519PublicKey.from_public_bytes(ephemeral)))
    try:
        return tuple(
            HKDF(algorithm=hashes.SHA256(), length=32, salt=client_nonce + server_nonce,
                 info=b"ai.market trust channel v1 aes-256-gcm " + direction).derive(shared)
            for direction in (b"c2s", b"s2c")
        )
    finally:
        # Best effort: Python/native temporary copies cannot be guaranteed erased.
        shared[:] = b"\x00" * len(shared)


def validate_sequence(sequence):
    if type(sequence) is not int or not 0 <= sequence < LIMIT:
        raise ValueError("Trust Channel sequence out of range")


def derive_iv(nonce: bytes, sequence: int, *, from_server: bool) -> bytes:
    validate_sequence(sequence)
    if len(nonce) != 12:
        raise ValueError("Server nonce must be 12 bytes")
    suffix = ((int.from_bytes(nonce[4:], "big") ^ sequence) & (LIMIT - 1)) | (int(from_server) << 63)
    return nonce[:4] + suffix.to_bytes(8, "big")


@dataclass(repr=False)
class TrafficSession:
    c2s: bytes
    s2c: bytes
    nonce: bytes
    outbound: int = 0
    inbound: int = -1

    def encrypt(self, message: dict) -> dict:
        sequence = self.outbound
        iv = derive_iv(self.nonce, sequence, from_server=False)
        plaintext = json.dumps(message).encode("utf-8")
        # Reserve before encryption/send: even a failed send must never reuse an IV.
        self.outbound += 1
        encrypted = AESGCM(self.c2s).encrypt(iv, plaintext, None)
        return {"type": "data", "sequence": sequence,
                "ciphertext": base64.b64encode(encrypted[:-16]).decode("ascii"),
                "auth_tag": base64.b64encode(encrypted[-16:]).decode("ascii")}

    def receive(self, frame: dict) -> dict | None:
        sequence = frame.get("sequence")
        validate_sequence(sequence)
        if sequence <= self.inbound:
            raise ValueError("Replayed or older Trust Channel sequence")
        if frame["type"] == "data":
            plaintext = AESGCM(self.s2c).decrypt(
                derive_iv(self.nonce, sequence, from_server=True),
                decode_b64(frame.get("ciphertext")) + decode_b64(frame.get("auth_tag"), 16), None,
            )
            payload = json.loads(plaintext)
        else:  # event frames are plaintext over authenticated WSS
            payload = frame.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("Trust Channel application payload must be an object")
        self.inbound = sequence
        return payload
