"""
S3 broker client for ai.market-backed S3 access.

The AIM Data container must not hold AWS credentials or assume seller roles.
All seller S3 access goes through ai.market's authenticated broker endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.config import settings
from app.services.serial_store import get_serial_store

DEFAULT_TIMEOUT = 15.0


class S3BrokerError(RuntimeError):
    """Raised when the S3 broker rejects or cannot complete a request."""


class S3BrokerNotActivatedError(S3BrokerError):
    """Raised when AIM Data has no serial/install token for broker auth."""


@dataclass(frozen=True)
class S3BrokerCredentials:
    serial: str
    install_token: str


class S3BrokerClient:
    """HTTP client for ai.market S3 connection broker endpoints."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._base_url = (base_url or settings.aimarket_url).rstrip("/")
        self._timeout = timeout

    def get_external_id(self) -> str:
        data = self._request("GET", "/api/v1/s3-connections/external-id")
        external_id = data.get("external_id")
        if not external_id:
            raise S3BrokerError("S3 broker did not return an ExternalId.")
        return str(external_id)

    def verify(self, *, role_arn: str, region: str, bucket: str, prefix: Optional[str] = None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/s3-connections/verify",
            json={
                "role_arn": role_arn,
                "region": region,
                "bucket": bucket,
                "prefix": prefix,
            },
        )

    def list_objects(
        self,
        *,
        role_arn: str,
        region: str,
        bucket: str,
        prefix: Optional[str] = None,
        continuation_token: Optional[str] = None,
        max_keys: Optional[int] = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/s3-connections/list-objects",
            json={
                "role_arn": role_arn,
                "region": region,
                "bucket": bucket,
                "prefix": prefix,
                "continuation_token": continuation_token,
                "max_keys": max_keys,
            },
        )

    def presign_object(self, *, role_arn: str, region: str, bucket: str, object_key: str) -> dict[str, Any]:
        data = self._request(
            "POST",
            "/api/v1/s3-connections/presign-object",
            json={
                "role_arn": role_arn,
                "region": region,
                "bucket": bucket,
                "object_key": object_key,
            },
        )
        if data.get("status") == "error":
            raise S3BrokerError(str(data.get("error_message") or "S3 broker presign failed."))
        url = data.get("url")
        if not url:
            raise S3BrokerError("S3 broker did not return a presigned URL.")
        return data

    def _credentials(self) -> S3BrokerCredentials:
        state = get_serial_store().state
        if not state.serial or not state.install_token:
            raise S3BrokerNotActivatedError(
                "AIM Data is not connected/activated. Complete activation before using S3 connections."
            )
        return S3BrokerCredentials(serial=state.serial, install_token=state.install_token)

    def _request(self, method: str, path: str, *, json: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        credentials = self._credentials()
        url = f"{self._base_url}{path}"
        params = {"serial": credentials.serial}
        headers = {"Authorization": f"Bearer {credentials.install_token}"}
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.request(method, url, params=params, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise S3BrokerError("S3 broker is unavailable. Retry after AIM Data reconnects to ai.market.") from exc

        try:
            data = response.json()
        except ValueError:
            data = {}

        if response.status_code in (401, 403):
            raise S3BrokerError("S3 broker authentication failed. Re-activate AIM Data and retry.")
        if response.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else None
            raise S3BrokerError(str(detail or f"S3 broker request failed with HTTP {response.status_code}."))
        if not isinstance(data, dict):
            raise S3BrokerError("S3 broker returned an invalid response.")
        return data
