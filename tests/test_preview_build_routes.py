"""Synthetic local source and authenticated identities; never real data or egress."""

import time
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.auth.api_key_auth import get_current_user, AuthenticatedUser
from app.routers.preview_builds import get_build_service
from app.routers.marketplace_publish import router
from app.services.preview_build_service import PreviewBuildService

OWNER = "00000000-0000-4000-8000-000000000002"
OTHER = "00000000-0000-4000-8000-000000000003"


@pytest.fixture
def setup(tmp_path):
    root = tmp_path.resolve()
    source = root / "source.ndjson"
    source.write_text('{"color":"blue"}\n{"color":"green"}\n')
    record = SimpleNamespace(
        id="dataset", upload_path=source, metadata={"preview_owner_id": OWNER}
    )
    processing = SimpleNamespace(
        get_dataset=lambda id: record if id == "dataset" else None,
        _save_record=lambda *args: None,
    )
    service = PreviewBuildService(root / "jobs", processing, root)
    app = FastAPI()
    app.include_router(router, prefix="/marketplace")
    app.dependency_overrides[get_build_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=OWNER, key_id="authenticated-test", scopes=["read", "write"]
    )
    with TestClient(app) as client:
        yield client, service, app, record
    for live in list(service.live.values()):
        live["cancel"].set()
        live["thread"].join(5)


def create(client):
    r = client.post(
        "/marketplace/preview-builds",
        json={
            "dataset_id": "dataset",
            "parsing": {"format": "ndjson", "encoding": "utf-8"},
            "schema_descriptors": [["color", "string", False, {}]],
        },
    )
    assert r.status_code == 200, r.text
    id = r.json()["job_id"]
    for _ in range(200):
        r = client.get("/marketplace/preview-builds/" + id)
        if r.json()["review_ready"]:
            return id
        if r.json()["state"] == "failed":
            pytest.fail(r.text)
        time.sleep(0.05)
    pytest.fail("worker did not complete")


def test_authenticated_owner_checks_all_routes(setup):
    client, service, app, record = setup
    id = create(client)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=OTHER, key_id="authenticated-test", scopes=["write"]
    )
    base = "/marketplace/preview-builds/" + id
    for route, method, body in [
        ("", "get", None),
        ("/rows", "get", None),
        ("/cancel", "post", {}),
        ("/selection", "put", {"leaf_indices": [0], "display_columns": ["color"]}),
        (
            "/policy",
            "post",
            {
                "rights_basis": "owner",
                "public_preview_permission": True,
                "restricted_content_confirmed": True,
            },
        ),
        ("/package", "post", {"destination": "local"}),
        ("/package", "get", None),
        ("/origin-check", "post", {"url": "https://seller.example/p"}),
        ("/submit", "post", {}),
        ("/withdraw", "post", {}),
    ]:
        response = getattr(client, method)(
            base + route, **({"json": body} if body is not None else {})
        )
        assert response.status_code == 403, (route, response.text)
    assert client.get("/marketplace/preview-builds/missing").status_code == 404
    assert (
        client.post(
            "/marketplace/preview-builds", json={"dataset_id": "dataset"}
        ).status_code
        == 403
    )


def test_caps_no_rows_and_cancel(setup):
    client, service, app, record = setup
    id = create(client)
    base = "/marketplace/preview-builds/" + id
    status = client.get(base)
    assert "blue" not in status.text and "green" not in status.text
    rows = client.get(base + "/rows")
    assert rows.headers["cache-control"] == "no-store"
    assert {r["cells"]["color"] for r in rows.json()["items"]} == {"blue", "green"}
    selected = client.put(
        base + "/selection", json={"leaf_indices": [0, 1], "display_columns": ["color"]}
    )
    assert selected.status_code == 200
    assert selected.json()["selection"]["rows"] == 2
    assert selected.json()["selection"]["canonical_bytes"] > 0
    for indices, columns, code in [
        (list(range(101)), ["color"], "rows_limit"),
        ([0], ["x"] * 26, "fields_limit"),
        ([0, 0], ["color"], "invalid_selection"),
    ]:
        r = client.put(
            base + "/selection",
            json={"leaf_indices": indices, "display_columns": columns},
        )
        assert r.status_code == 422 and r.json()["detail"] == code
    for body in [
        {"destination": "../../escape"},
        {"destination": "local", "path": "/tmp/secret"},
    ]:
        r = client.post(base + "/package", json=body)
        assert r.status_code == 422 and "/tmp/secret" not in r.text
    assert client.post(base + "/cancel", json={}).json()["state"] == "cancelled"
    assert not list((service.root / "worker").glob("job-*"))
    assert client.get(base + "/rows").status_code == 409


def test_missing_ownership_and_declarations(setup):
    client, service, app, record = setup
    assert (
        client.post(
            "/marketplace/preview-builds", json={"dataset_id": "dataset"}
        ).json()["detail"]
        == "parsing_declaration_required"
    )
    record.metadata.clear()
    assert (
        client.post(
            "/marketplace/preview-builds", json={"dataset_id": "dataset"}
        ).status_code
        == 403
    )
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=OWNER, key_id="mock_key_auth_disabled", scopes=["write"]
    )
    assert (
        client.get("/marketplace/preview-builds?dataset_id=dataset").status_code == 401
    )


def test_restart_rebuilds_same_index(setup):
    client, service, app, record = setup
    id = create(client)
    base = "/marketplace/preview-builds/" + id
    before = client.get(base + "/rows").json()
    live = service.live[id]
    live["cancel"].set()
    live["thread"].join(5)
    replacement = PreviewBuildService(
        service.root, service.processing, service.upload_root
    )
    app.dependency_overrides[get_build_service] = lambda: replacement
    try:
        for _ in range(200):
            response = client.get(base)
            if response.json()["review_ready"]:
                break
            time.sleep(0.05)
        assert client.get(base + "/rows").json() == before
        assert (
            client.get("/marketplace/preview-builds?dataset_id=dataset").json()[
                "job_id"
            ]
            == id
        )
        assert "blue" not in (service.root / "jobs.sqlite").read_bytes().decode(
            errors="ignore"
        )
    finally:
        for live in list(replacement.live.values()):
            live["cancel"].set()
            live["thread"].join(5)


def test_sealed_scan_export_sign_and_local_submit(setup, monkeypatch, tmp_path):
    from datetime import datetime, timezone, timedelta
    from app.services import preview_content_policy as policy
    from app.core.crypto import DeviceCrypto
    from app.services.preview_signing_service import (
        PreviewSigningService,
        fingerprint,
        public_bytes,
    )

    client, service, app, record = setup
    monkeypatch.setattr(policy, "detector_identity", lambda: policy.DETECTOR_IDENTITY)
    crypto = DeviceCrypto(str(tmp_path.resolve() / "key.json"), "synthetic-passphrase")
    crypto._pbkdf2_iterations = 1
    keys = crypto.get_or_create_keypairs()
    install = "00000000-0000-4000-8000-000000000001"
    signer = PreviewSigningService(
        crypto,
        install_id=install,
        seller_id=OWNER,
        evidence_reader=lambda: dict(
            install_id=install,
            seller_id=OWNER,
            fingerprint=fingerprint(public_bytes(keys[1])),
            status="active",
            observed_at=datetime.now(timezone.utc),
        ),
        evidence_max_age=timedelta(hours=1),
    )
    monkeypatch.setattr(service, "signer", lambda job: signer)
    p1 = {
        k: "00000000-0000-4000-8000-000000000004"
        for k in ("summary_id", "summary_approval_id", "content_revision", "listing_id")
    }
    p1.update(
        {
            k: "a" * 64
            for k in (
                "summary_hash",
                "render_hash",
                "aggregate_hash",
                "source_revision",
            )
        },
        listing_version_id=None,
    )
    record.metadata["preview_local_approval"] = {
        "owner": OWNER,
        "references": p1,
        "digest": "b" * 64,
    }
    id = create(client)
    base = "/marketplace/preview-builds/" + id
    p1["source_revision"] = client.get(base).json()["source_version"]
    assert (
        client.put(
            base + "/selection",
            json={"leaf_indices": [0], "display_columns": ["color"]},
        ).status_code
        == 200
    )
    consent = {
        "rights_basis": "owner",
        "public_preview_permission": True,
        "restricted_content_confirmed": True,
    }
    scanned = client.post(base + "/policy", json=consent)
    assert scanned.status_code == 200, scanned.text
    assert scanned.json()["passed"], scanned.text
    packaged = client.post(base + "/package", json={"destination": "export"})
    assert packaged.status_code == 200, packaged.text
    pub = packaged.json()["publication"]
    download = client.get(base + "/package")
    assert download.status_code == 200 and "row" in download.json()["entries"][0]
    from email.message import Message
    from app.services.preview_origin_service import capture_receipt

    def receipts(url, **opts):
        headers = Message()
        for k, v in {
            "Content-Type": "application/vnd.aim.preview+json",
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "https://ai.market",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
        }.items():
            headers[k] = v
        return [
            capture_receipt(
                url,
                m,
                410 if opts.get("retired") and m == "GET" else 200,
                headers,
                captured_at=datetime.now(timezone.utc),
                origin="https://ai.market",
                retired=opts.get("retired", False),
            )
            for m in ["GET", "OPTIONS"]
        ]

    monkeypatch.setattr(
        "app.services.preview_build_service.verify_hosted_package", receipts
    )
    assert (
        client.post(
            base + "/origin-check",
            json={"url": "https://seller.example/" + pub["relative_path"]},
        ).status_code
        == 200
    )
    signed = client.post(
        base + "/candidate", json={**consent, "metadata_accuracy_confirmed": True}
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["candidate"]["kind"] == "fixture_candidate"

    def outbound(*args, **kwargs):
        pytest.fail("outbound call during submit")

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "post", outbound)
    monkeypatch.setattr("socket.socket.connect", outbound)
    response = client.post(base + "/submit", json={})
    assert response.status_code == 200, response.text
    assert (
        response.json()["outcome"]
        == "Prepared locally; marketplace preview submission awaits backend support"
    )
    assert "blue" not in response.text and "green" not in response.text
    assert (
        client.post(base + "/submit", json={}).json()["candidate"]
        == signed.json()["candidate"]
    )
    record.metadata["preview_local_approval"]["digest"] = "c" * 64
    assert (
        client.post(base + "/submit", json={}).json()["detail"]
        == "metadata_approval_changed"
    )
    record.metadata["preview_local_approval"]["digest"] = "b" * 64
    refreshed = client.post(
        base + "/refresh", json={**consent, "metadata_accuracy_confirmed": True}
    )
    assert refreshed.status_code == 200, refreshed.text
    newer = "/marketplace/preview-builds/" + refreshed.json()["job_id"]
    for _ in range(200):
        state = client.get(newer).json()
        if state["review_ready"]:
            break
        time.sleep(0.05)
    assert state["prior_job_id"] == id
    assert state["selection"]["leaf_indices"] == [0]
    assert client.post(newer + "/policy", json=consent).json()["passed"]
    publication = client.post(
        newer + "/package", json={"destination": "export"}
    ).json()["publication"]
    assert publication["sample_hash"] == pub["sample_hash"]
    assert publication["disclosure_version"] != pub["disclosure_version"]
    assert (
        client.post(
            newer + "/origin-check",
            json={"url": "https://seller.example/" + publication["relative_path"]},
        ).status_code
        == 200
    )
    candidate = client.post(
        newer + "/candidate", json={**consent, "metadata_accuracy_confirmed": True}
    )
    assert candidate.status_code == 200, candidate.text
    import json

    saved = service.load(refreshed.json()["job_id"], OWNER)
    binding = json.loads(service.journal.read(tuple(saved["journal_key"]))["candidate"])
    assert binding["supersedes"] == pub["disclosure_version"]
    assert binding["sample_hash"] == pub["sample_hash"]
    assert client.post(base + "/withdraw", json={}).json()["state"] == "retired"
    assert client.get(base + "/package").status_code == 409


def test_approval_digest_is_owner_scoped_local_and_idempotent(setup):
    client, service, app, record = setup
    body = {"dataset_id": "dataset", "approved_metadata_digest": "a" * 64}
    first = client.post("/marketplace/preview-builds/metadata-approval", json=body)
    assert first.status_code == 200
    assert first.json()["kind"] == "local_metadata_approval"
    assert (
        client.post("/marketplace/preview-builds/metadata-approval", json=body).json()
        == first.json()
    )
    assert client.post(
        "/marketplace/preview-builds/metadata-approval",
        json={**body, "rows": [{"secret": "synthetic-marker"}]},
    ).json() == {"detail": "invalid_options"}
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=OTHER, key_id="authenticated-test", scopes=["write"]
    )
    assert (
        client.post(
            "/marketplace/preview-builds/metadata-approval", json=body
        ).status_code
        == 403
    )


def test_missing_authentication_is_rejected(setup, monkeypatch):
    client, service, app, record = setup
    app.dependency_overrides.pop(get_current_user)
    monkeypatch.setattr("app.auth.api_key_auth._is_auth_enabled", lambda: True)
    assert (
        client.get("/marketplace/preview-builds?dataset_id=dataset").status_code == 401
    )


def test_source_change_invalidates_without_returning_rows(setup):
    client, service, app, record = setup
    id = create(client)
    record.upload_path.write_text('{"color":"changed"}\n')
    response = client.get("/marketplace/preview-builds/" + id + "/rows")
    assert response.status_code == 409 and response.json()["detail"] == "source_changed"
    assert "changed" not in response.text.replace("source_changed", "")


def test_original_source_path_must_be_app_managed(setup, tmp_path):
    client, service, app, record = setup
    source = record.upload_path
    record.upload_path = tmp_path.resolve() / "symlink.ndjson"
    record.upload_path.symlink_to(source)
    assert (
        client.post(
            "/marketplace/preview-builds", json={"dataset_id": "dataset"}
        ).json()["detail"]
        == "source_not_allowed"
    )


def test_sealed_scan_unavailable_never_exports(setup, monkeypatch):
    client, service, app, record = setup
    id = create(client)
    base = "/marketplace/preview-builds/" + id
    client.put(
        base + "/selection", json={"leaf_indices": [0], "display_columns": ["color"]}
    )
    from app.services.preview_content_policy import PolicyError

    def unavailable():
        raise PolicyError("detector_unavailable")

    monkeypatch.setattr(
        "app.services.preview_content_policy.detector_identity", unavailable
    )
    response = client.post(
        base + "/policy",
        json={
            "rights_basis": "owner",
            "public_preview_permission": True,
            "restricted_content_confirmed": True,
        },
    )
    assert response.json() == {
        "policy": "aim-preview-policy-v1",
        "version": "1.0.0",
        "passed": False,
        "reason_codes": ["detector_unavailable"],
    }
    assert (
        client.post(base + "/package", json={"destination": "export"}).json()["detail"]
        == "rescan_required"
    )
