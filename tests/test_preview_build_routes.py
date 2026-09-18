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
    app.include_router(router)
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
    assert not list((service.root / "worker").rglob("job-*"))
    assert id not in service.live
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


def test_reload_reattaches_and_restart_expires(setup):
    client, service, app, record = setup
    id = create(client)
    base = "/marketplace/preview-builds/" + id
    live = service.live[id]
    directory = live["builder"].tree.directory
    assert client.get(base).json()["review_ready"]
    assert (
        client.get("/marketplace/preview-builds?dataset_id=dataset").json()["job_id"]
        == id
    )
    assert service.live[id] is live
    live["cancel"].set()
    live["thread"].join(5)
    replacement = PreviewBuildService(
        service.root, service.processing, service.upload_root
    )
    app.dependency_overrides[get_build_service] = lambda: replacement
    for url in [base, "/marketplace/preview-builds?dataset_id=dataset"]:
        response = client.get(url)
        assert response.status_code == 200
        assert response.json()["state"] == "expired"
        assert response.json()["code"] == "review_expired"
        assert not response.json()["review_ready"]
    assert client.get(base + "/rows").json()["detail"] == "review_expired"
    assert not replacement.live and not directory.exists()
    assert "blue" not in (service.root / "jobs.sqlite").read_bytes().decode(
        errors="ignore"
    )


def test_duplicate_and_independent_owner_dataset_sessions(setup):
    client, service, app, record = setup
    id = create(client)
    body = {
        "dataset_id": "dataset",
        "parsing": {"format": "ndjson", "encoding": "utf-8"},
        "schema_descriptors": [["color", "string", False, {}]],
    }
    duplicate = client.post("/marketplace/preview-builds", json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == {"code": "job_already_running", "job_id": id}
    assert client.get("/marketplace/preview-builds/" + id).json()["review_ready"]
    assert (
        client.get("/marketplace/preview-builds?dataset_id=dataset").json()["job_id"]
        == id
    )
    records = {"dataset": record}
    service.processing.get_dataset = records.get
    for owner, dataset in [(OWNER, "second"), (OTHER, "other")]:
        records[dataset] = SimpleNamespace(
            id=dataset,
            upload_path=record.upload_path,
            metadata={"preview_owner_id": owner},
        )
        app.dependency_overrides[get_current_user] = lambda owner=owner: (
            AuthenticatedUser(
                user_id=owner, key_id="authenticated-test", scopes=["write"]
            )
        )
        if owner == OTHER:
            for url, code in [
                ("/marketplace/preview-builds/" + id, "job_owner_mismatch"),
                (
                    "/marketplace/preview-builds?dataset_id=dataset",
                    "dataset_owner_unverified",
                ),
            ]:
                denied = client.get(url)
                assert denied.status_code == 403 and denied.json()["detail"] == code
                assert id not in denied.text
            denied = client.post("/marketplace/preview-builds", json=body)
            assert (
                denied.status_code == 403
                and denied.json()["detail"] == "dataset_owner_unverified"
            )
        assert (
            client.get("/marketplace/preview-builds?dataset_id=" + dataset).json()
            is None
        )
        new = client.post(
            "/marketplace/preview-builds", json={**body, "dataset_id": dataset}
        )
        assert new.status_code == 200, new.text
        new_id = new.json()["job_id"]
        for _ in range(200):
            status = client.get("/marketplace/preview-builds/" + new_id).json()
            if status["review_ready"]:
                break
            assert status["state"] != "failed", status
            time.sleep(0.05)
        assert status["review_ready"]
        assert (
            client.get("/marketplace/preview-builds?dataset_id=" + dataset).json()[
                "job_id"
            ]
            == new_id
        )
    assert len(service.live) == 3
    assert len({live["builder"].tree.directory for live in service.live.values()}) == 3


@pytest.mark.parametrize("failure", ["start", "thread"])
def test_failed_start_never_leaves_building_row(setup, monkeypatch, failure):
    import json

    client, service, app, record = setup

    def fail(*args):
        raise RuntimeError("private diagnostic")

    if failure == "start":
        monkeypatch.setattr(service, "start", fail)
    else:
        import threading

        original_start = threading.Thread.start

        def fail_worker(thread):
            if (
                getattr(thread._target, "__qualname__", "")
                == "PreviewBuildService.start.<locals>.run"
            ):
                fail()
            return original_start(thread)

        monkeypatch.setattr(threading.Thread, "start", fail_worker)
    response = client.post(
        "/marketplace/preview-builds",
        json={
            "dataset_id": "dataset",
            "parsing": {"format": "ndjson", "encoding": "utf-8"},
            "schema_descriptors": [["color", "string", False, {}]],
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "build_start_failed"
    with service.db() as db:
        jobs = [json.loads(r[0]) for r in db.execute("SELECT payload FROM jobs")]
    assert len(jobs) == 1 and jobs[0]["state"] == "failed"
    assert jobs[0]["code"] == "build_start_failed"
    assert response.json()["detail"]["job_id"] == jobs[0]["id"]
    assert not service.live
    assert (
        client.get("/marketplace/preview-builds?dataset_id=dataset").json()["state"]
        == "failed"
    )


def test_idle_expiry_cleans_index_without_polling(setup):
    client, service, app, record = setup
    service.idle_seconds = 0.5
    id = create(client)
    live = service.live[id]
    directory = live["builder"].tree.directory
    # A real owner interaction renews the lease; no background heartbeat does.
    time.sleep(0.3)
    assert client.get("/marketplace/preview-builds/" + id + "/rows").status_code == 200
    time.sleep(0.3)
    assert id in service.live
    live["thread"].join(3)
    assert not live["thread"].is_alive()
    assert id not in service.live and not directory.exists()
    status = client.get("/marketplace/preview-builds/" + id).json()
    assert status["state"] == "expired" and status["code"] == "review_expired"
    assert not status["review_ready"]
    create(client)


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
    assert not packaged.json()["review_ready"]
    assert id not in service.live
    assert not list((service.root / "worker").rglob("job-*"))
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

    def reopen_legacy_review():
        # Exercise terminal cleanup even for a session retained by pre-R2 code.
        with service.lock:
            service.start(service.load(id, OWNER))
        for _ in range(200):
            with service.lock:
                if service.live.get(id, {}).get("builder"):
                    return
            time.sleep(0.05)
        pytest.fail("legacy review did not open")

    reopen_legacy_review()
    signed = client.post(
        base + "/candidate", json={**consent, "metadata_accuracy_confirmed": True}
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["candidate"]["kind"] == "fixture_candidate"
    assert id not in service.live and not signed.json()["review_ready"]

    def outbound(*args, **kwargs):
        pytest.fail("outbound call during submit")

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "post", outbound)
    monkeypatch.setattr("socket.socket.connect", outbound)
    reopen_legacy_review()
    response = client.post(base + "/submit", json={})
    assert response.status_code == 200, response.text
    assert (
        response.json()["outcome"]
        == "Prepared locally; marketplace preview submission awaits backend support"
    )
    assert "blue" not in response.text and "green" not in response.text
    assert id not in service.live and not response.json()["review_ready"]
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
    legacy = service.load(refreshed.json()["job_id"], OWNER)
    legacy.pop("proofs")
    service.save(legacy)
    candidate = client.post(
        newer + "/candidate", json={**consent, "metadata_accuracy_confirmed": True}
    )
    assert candidate.status_code == 200, candidate.text
    import json

    saved = service.load(refreshed.json()["job_id"], OWNER)
    binding = json.loads(service.journal.read(tuple(saved["journal_key"]))["candidate"])
    assert binding["supersedes"] == pub["disclosure_version"]
    assert binding["sample_hash"] == pub["sample_hash"]
    reopen_legacy_review()
    assert client.post(base + "/withdraw", json={}).json()["state"] == "retired"
    assert client.get(base + "/package").status_code == 409
    assert client.post(base + "/submit", json={}).json()["detail"] == "job_inactive"
    assert not service.live
    assert not list((service.root / "worker").rglob("job-*"))


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


def test_missing_detector_never_blocks_policy_or_package(setup, monkeypatch):
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
    assert response.json()["policy"] == "aim-preview-policy-v2"
    assert response.json()["version"] == "2.0.0"
    assert response.json()["passed"] is True
    assert response.json()["reason_codes"] == []
    packaged = client.post(base + "/package", json={"destination": "export"})
    assert packaged.status_code == 200
    assert packaged.json()["state"] == "packaged"


def test_exact_csv_date_and_city_reproduction_produces_package(setup):
    client, service, app, record = setup
    source = record.upload_path.with_name("readings.csv")
    source.write_text(
        "reading_id,station_city,sensor_class,reading_date,temperature_tenths_c,humidity_pct,is_calibrated\n"
        "550e8400-e29b-41d4-a716-446655440000,Lisbon,urban,2026-06-28,-123,58,true\n"
    )
    record.upload_path = source
    response = client.post(
        "/marketplace/preview-builds",
        json={
            "dataset_id": "dataset",
            "parsing": {"format": "csv", "encoding": "utf-8", "delimiter": ",", "quote": '"', "escape": "\\", "header": True, "locale": "C", "null_token": ""},
            "schema_descriptors": [
                ["reading_id", "string", False, {}], ["station_city", "string", False, {}],
                ["sensor_class", "string", False, {}], ["reading_date", "string", False, {}],
                ["temperature_tenths_c", "signed_integer", False, {}],
                ["humidity_pct", "signed_integer", False, {}], ["is_calibrated", "boolean", False, {}],
            ],
        },
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    for _ in range(200):
        status = client.get("/marketplace/preview-builds/" + job_id).json()
        if status["review_ready"]:
            break
        time.sleep(0.05)
    base = "/marketplace/preview-builds/" + job_id
    assert client.put(base + "/selection", json={"leaf_indices": [0], "display_columns": status["columns"]}).status_code == 200
    policy_result = client.post(base + "/policy", json={"rights_basis": "owner", "public_preview_permission": True, "restricted_content_confirmed": True})
    assert policy_result.json()["passed"] is True
    package = client.post(base + "/package", json={"destination": "export"})
    assert package.status_code == 200
    assert package.json()["state"] == "packaged"


@pytest.mark.parametrize("action", ["cancel", "idle"])
def test_queued_build_is_live_cancellable_and_bounded(setup, action):
    from app.services.dataset_merkle_service import private_job

    client, service, app, record = setup
    service.idle_seconds = 0.2
    with private_job(service.root / "worker"):
        response = client.post(
            "/marketplace/preview-builds",
            json={
                "dataset_id": "dataset",
                "parsing": {"format": "ndjson", "encoding": "utf-8"},
                "schema_descriptors": [["color", "string", False, {}]],
            },
        )
        assert response.status_code == 200, response.text
        id = response.json()["job_id"]
        assert response.json()["state"] == "building" and id in service.live
        live = service.live[id]
        if action == "cancel":
            response = client.post(
                "/marketplace/preview-builds/" + id + "/cancel", json={}
            )
            assert (
                response.status_code == 200 and response.json()["state"] == "cancelled"
            )
        live["thread"].join(3)
        assert id not in service.live
    assert not list((service.root / "worker").rglob("job-*"))
    if action == "idle":
        status = client.get("/marketplace/preview-builds/" + id).json()
        assert status["state"] == "expired" and status["code"] == "review_expired"


def test_idle_timeout_configuration(setup, monkeypatch, tmp_path):
    client, service, app, record = setup
    assert service.idle_seconds == 1800
    monkeypatch.setenv("PREVIEW_REVIEW_IDLE_SECONDS", "75")
    assert (
        PreviewBuildService(
            tmp_path.resolve() / "configured", service.processing, service.upload_root
        ).idle_seconds
        == 75
    )
    for value in ["0", "-1", "nan", "inf"]:
        monkeypatch.setenv("PREVIEW_REVIEW_IDLE_SECONDS", value)
        with pytest.raises(ValueError, match="invalid_review_idle_timeout"):
            PreviewBuildService(
                tmp_path.resolve() / "invalid", service.processing, service.upload_root
            )


def test_production_mount_requires_authentication(monkeypatch):
    """Use main.py's real prefix/admin wrapper, with auth enabled, not test bypass."""
    from fastapi import Depends
    from app.middleware.auth import require_admin
    from app.auth import api_key_auth

    monkeypatch.setattr(api_key_auth, "_is_auth_enabled", lambda: True)
    application = FastAPI()
    application.include_router(router, prefix="/api", dependencies=[Depends(require_admin)])
    with TestClient(application) as client:
        for method in ("GET", "POST"):
            response = client.request(method, "/api/marketplace/preview-builds")
            assert response.status_code == 401
        assert client.get("/api/preview-builds").status_code == 404
