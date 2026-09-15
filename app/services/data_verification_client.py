"""Fail-closed signed client for the ai.market data-verification control plane."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from urllib.parse import urlsplit
import ssl

import httpx

from app.schemas.data_verification import (
    LifecycleCommand,
    PaymentLifecycleStatus,
    QuoteProbeRequest,
    QuoteResponse,
    ReportIngestResponse,
    ScanSpecIssueRequest,
)
from app.services.data_verification.contract import ScanSpecIssueResponse, log_platform_key_event
from app.services.marketplace_action_signer import (
    build_action_jwt,
    canonical_json_bytes,
    canonical_payload_hash,
)


class DataVerificationClientError(RuntimeError):
    """A display-safe control-plane failure with no reflected response body."""


PayInReadinessState = Literal["setup_required", "setup_pending", "ready", "blocked"]


@dataclass(frozen=True)
class LifecycleCommandResult:
    status: PaymentLifecycleStatus
    server_date_utc: datetime | None


class DataVerificationClient:
    def __init__(
        self,
        *,
        base_url: str,
        seller_id: str,
        install_id: str,
        install_private_key: Any,
        seller_access_token: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._origin = self._validated_origin(base_url)
        parsed = urlsplit(base_url)
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise DataVerificationClientError("ai.market origin is invalid")
        self._base_url = base_url.rstrip("/")
        if http_client is not None:
            # Injected production clients must retain certificate/hostname checks.
            transports = [http_client._transport, *http_client._mounts.values()]
            for transport in transports:
                context = getattr(getattr(transport, "_pool", None), "_ssl_context", None)
                if context is not None and (
                    context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname
                ):
                    raise DataVerificationClientError("ai.market TLS verification is required")
        self._seller_id = seller_id
        self._install_id = install_id
        self._install_private_key = install_private_key
        self._seller_access_token = seller_access_token
        self._http_client = http_client

    @staticmethod
    def _validated_origin(url: str) -> tuple[str, str, int]:
        try:
            parsed = urlsplit(url)
            if (
                any(char.isspace() for char in url)
                or "\\" in url
                or parsed.username is not None or parsed.password is not None
                or not parsed.hostname
            ):
                raise ValueError
            # ai_market_url already permits these local test hosts, including
            # the S1656 Docker override. The payment handoff validator is separate.
            local_http = parsed.scheme == "http" and parsed.hostname in {
                "localhost", "127.0.0.1", "::1", "host.docker.internal",
            }
            if parsed.scheme != "https" and not local_http:
                raise ValueError
            port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
            return (parsed.scheme, parsed.hostname, port)
        except (TypeError, ValueError):
            raise DataVerificationClientError("ai.market origin is invalid") from None

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self._base_url}{path}"
        if not path.startswith("/") or self._validated_origin(url) != self._origin:
            raise DataVerificationClientError("ai.market origin is invalid")
        kwargs["follow_redirects"] = False
        try:
            if self._http_client is not None:
                response = await self._http_client.request(method, url, **kwargs)
            else:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise DataVerificationClientError("ai.market data verification timed out") from exc
        except httpx.RequestError as exc:
            raise DataVerificationClientError("ai.market data verification is unavailable") from exc
        if self._validated_origin(str(response.url)) != self._origin or response.history:
            raise DataVerificationClientError("ai.market origin is invalid")
        if not response.is_success:
            raise DataVerificationClientError(
                f"ai.market data verification refused the request ({response.status_code})"
            )
        return response

    async def _signed_json(
        self,
        method: str,
        path: str,
        *,
        expected_action: str,
        body: dict[str, Any],
    ) -> httpx.Response:
        token = build_action_jwt(
            seller_id=self._seller_id,
            install_id=self._install_id,
            action=expected_action,
            payload_hash=canonical_payload_hash(body),
            private_key=self._install_private_key,
        )
        return await self._request(
            method,
            path,
            content=canonical_json_bytes(body),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )

    async def payment_method_readiness(self) -> PayInReadinessState:
        """Check card readiness only at the explicit paid-service boundary."""
        response = await self._request(
            "GET",
            "/api/v1/data-verification/payment-method/readiness",
            headers={"Authorization": f"Bearer {self._seller_access_token}"},
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise DataVerificationClientError(
                "ai.market returned an invalid payment-readiness response"
            ) from exc
        expected_keys = {
            "version", "state", "can_start_setup", "can_replace_payment_method", "message"
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            raise DataVerificationClientError(
                "ai.market returned an invalid payment-readiness response"
            )
        state = payload.get("state")
        expected_flags = {
            "setup_required": (True, False),
            "setup_pending": (False, False),
            "ready": (False, True),
            "blocked": (False, False),
        }
        if (
            payload.get("version") != "data_verification_payin_readiness_v1"
            or not isinstance(state, str)
            or state not in expected_flags
            or not isinstance(payload.get("message"), str)
            or not isinstance(payload.get("can_start_setup"), bool)
            or not isinstance(payload.get("can_replace_payment_method"), bool)
            or (
                payload.get("can_start_setup"),
                payload.get("can_replace_payment_method"),
            )
            != expected_flags[state]
        ):
            raise DataVerificationClientError(
                "ai.market returned an invalid payment-readiness response"
            )
        return state

    async def quote(self, probe: QuoteProbeRequest) -> QuoteResponse:
        response = await self._signed_json(
            "POST",
            "/api/v1/data-verification/quote",
            expected_action="data_verification_quote",
            body=probe.model_dump(mode="json"),
        )
        return QuoteResponse.model_validate(response.json())

    async def start(self, request: ScanSpecIssueRequest) -> ScanSpecIssueResponse:
        response = await self._signed_json(
            "POST",
            "/api/v1/data-verification/scan-spec",
            expected_action="data_verification_start",
            body=request.model_dump(mode="json"),
        )
        try:
            return ScanSpecIssueResponse.model_validate(response.json())
        except (ValueError, TypeError):
            log_platform_key_event("scan_spec_response_invalid")
            raise DataVerificationClientError(
                "ai.market returned an invalid scan-spec response"
            ) from None

    async def ingest_report(self, report: dict[str, Any]) -> ReportIngestResponse:
        response = await self._request(
            "PUT",
            "/api/v1/data-verification/scan-spec",
            content=canonical_json_bytes(report),
            headers={"Content-Type": "application/json"},
        )
        return ReportIngestResponse.model_validate(response.json())

    async def status(self, verification_id: str) -> PaymentLifecycleStatus:
        response = await self._request(
            "GET",
            f"/api/v1/data-verification/{verification_id}/status",
            headers={"Authorization": f"Bearer {self._seller_access_token}"},
        )
        return PaymentLifecycleStatus.model_validate(response.json())

    async def command(self, command: LifecycleCommand) -> LifecycleCommandResult:
        action = command.requested_action
        response = await self._signed_json(
            "POST",
            f"/api/v1/data-verification/{command.verification_id}/{action}",
            expected_action=f"data_verification_{action}",
            body=command.model_dump(mode="json"),
        )
        server_date = response.headers.get("Date")
        observed_at = None
        if server_date:
            try:
                observed_at = parsedate_to_datetime(server_date).astimezone(timezone.utc)
            except (TypeError, ValueError):
                observed_at = None
        return LifecycleCommandResult(
            status=PaymentLifecycleStatus.model_validate(response.json()),
            server_date_utc=observed_at,
        )
