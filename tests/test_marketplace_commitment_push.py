import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch
from app.routers import marketplace_publish as route
from app.services.marketplace_push_service import prepare_preview_request
from app.services.preview_signing_service import SigningError, construct_request, LocalCandidate
from tests.preview_fixture_factory import request_fixture, material, p1
from tests.test_preview_signing_service import signer


def test_constructor_uses_loaded_registered_key(signer):
    s, _ = signer
    _, c, b = material()
    ref = s.signer_reference
    c['aim_data_signer_reference'] = ref
    b['signer_reference'] = ref
    for proof in c['proofs']:
        proof['signer_reference'] = ref
        proof.update(s.sign_proof(c, proof))
    c = s.sign_commitment(c)
    from app.services.preview_content_policy import scan_attestation_digest
    b['scan_attestation_digest'] = scan_attestation_digest(c['proofs'])
    r = construct_request(LocalCandidate.validate(b), c, c['proofs'], signer=s, approved_p1=p1(b))
    assert r['binding'] == b
    with pytest.raises(SigningError, match='p1_reference_mismatch'):
        construct_request(LocalCandidate.validate(b), c, c['proofs'], signer=s, approved_p1={})


def test_new_arm_never_submits():
    with patch('httpx.AsyncClient') as net:
        with pytest.raises(SigningError, match='preview_integration_not_yet_available'):
            prepare_preview_request(request_fixture())
        net.assert_not_called()


def test_router_closed_and_gated():
    app = FastAPI(); app.include_router(route.router, prefix='/api')
    app.dependency_overrides[route.get_current_user] = lambda: {'id': 'fixture'}
    with TestClient(app) as client, patch('httpx.AsyncClient') as net:
        r = request_fixture()
        url = '/api/marketplace/listings/' + r['binding']['listing_id'] + '/at-a-glance/approve'
        assert client.post(url, json=r).json() == {'detail': 'preview_integration_not_yet_available'}
        r['binding']['raw_marker'] = 'ZERO_INGRESS_SYNTHETIC_MARKER'
        response = client.post(url, json=r)
        assert response.status_code == 422
        assert 'ZERO_INGRESS' not in response.text
        net.assert_not_called()


def test_legacy_rows_not_in_model():
    assert 'DisclosureApprovedSample' not in vars(route)
    with pytest.raises(ValueError):
        route.DisclosureSnapshotProxyRequest(
            dataset_id='fixture', approved_fields={}, sample_decision='approved_rows',
            approved_sample={'columns':['x'], 'rows':[{'x':'ZERO_INGRESS_SYNTHETIC_MARKER'}], 'row_refs':['0']},
            ai_training_notification_ack=True, ai_training_notification_text='fixture',
            license='fixture', approval_source='aim_channel', source_publish_operation_id='fixture')
