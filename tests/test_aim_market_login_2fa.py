from collections import deque

import httpx
import pytest
from fastapi import HTTPException

from app.routers import auth


class FakeAsyncClient:
    post_responses = deque()
    get_responses = deque()
    post_calls = []
    get_calls = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, json):
        self.post_calls.append({"url": url, "json": json})
        response = self.post_responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    async def get(self, url, headers):
        self.get_calls.append({"url": url, "headers": headers})
        response = self.get_responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def mock_ai_market(monkeypatch):
    FakeAsyncClient.post_responses = deque()
    FakeAsyncClient.get_responses = deque()
    FakeAsyncClient.post_calls = []
    FakeAsyncClient.get_calls = []

    handled_tokens = []

    async def fake_handle_token(access_token, user_data=None, db=None):
        handled_tokens.append({"access_token": access_token, "user_data": user_data, "db": db})

    monkeypatch.setattr(auth.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(auth, "_handle_ai_market_token", fake_handle_token)
    monkeypatch.setattr(auth.settings, "ai_market_url", "https://ai.market.test")
    monkeypatch.setattr(auth.settings, "keystore_passphrase", None)

    yield handled_tokens


def token_response(access_token="access-token", refresh_token="refresh-token"):
    return httpx.Response(
        200,
        json={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "onboarding_required": False,
            "onboarding_step": None,
        },
    )


def me_response():
    return httpx.Response(
        200,
        json={
            "id": "seller-123",
            "email": "seller@example.com",
            "role": "seller",
        },
    )


@pytest.mark.asyncio
async def test_aim_market_login_non_2fa_success_unchanged(mock_ai_market):
    FakeAsyncClient.post_responses.append(token_response())
    FakeAsyncClient.get_responses.append(me_response())

    result = await auth.aim_market_login(
        {"email": "seller@example.com", "password": "secret"},
        request=None,
        db=None,
    )

    assert result["access_token"] == "access-token"
    assert result["refresh_token"] == "refresh-token"
    assert result["token_type"] == "bearer"
    assert result["onboarding_required"] is False
    assert result["onboarding_step"] is None
    assert result["user"]["id"] == "seller-123"
    assert FakeAsyncClient.post_calls == [
        {
            "url": "https://ai.market.test/api/v1/auth/login",
            "json": {"email": "seller@example.com", "password": "secret"},
        }
    ]
    assert FakeAsyncClient.get_calls[0]["url"] == "https://ai.market.test/api/v1/auth/me"
    assert mock_ai_market[0]["access_token"] == "access-token"


@pytest.mark.asyncio
async def test_aim_market_login_returns_2fa_challenge(mock_ai_market):
    FakeAsyncClient.post_responses.append(
        httpx.Response(200, json={"pre_auth_token": "pre-auth-token"})
    )

    result = await auth.aim_market_login(
        {"email": "seller@example.com", "password": "secret"},
        request=None,
        db=None,
    )

    assert result == {"requires_2fa": True, "pre_auth_token": "pre-auth-token"}
    assert FakeAsyncClient.get_calls == []
    assert mock_ai_market == []


@pytest.mark.asyncio
async def test_aim_market_login_2fa_verify_success_returns_full_token_bundle(mock_ai_market):
    FakeAsyncClient.post_responses.append(token_response(access_token="verified-access"))
    FakeAsyncClient.get_responses.append(me_response())

    result = await auth.aim_market_login(
        {
            "email": "seller@example.com",
            "pre_auth_token": "pre-auth-token",
            "code": "123456",
        },
        request=None,
        db=None,
    )

    assert result["access_token"] == "verified-access"
    assert result["refresh_token"] == "refresh-token"
    assert result["user"]["email"] == "seller@example.com"
    assert FakeAsyncClient.post_calls == [
        {
            "url": "https://ai.market.test/api/v1/auth/2fa/verify",
            "json": {"pre_auth_token": "pre-auth-token", "code": "123456"},
        }
    ]
    assert FakeAsyncClient.get_calls[0]["headers"] == {"Authorization": "Bearer verified-access"}
    assert mock_ai_market[0]["access_token"] == "verified-access"


@pytest.mark.asyncio
async def test_aim_market_login_2fa_wrong_code_returns_401_without_clearing_challenge(mock_ai_market):
    FakeAsyncClient.post_responses.append(httpx.Response(401, json={"detail": "bad code"}))

    with pytest.raises(HTTPException) as exc:
        await auth.aim_market_login(
            {
                "email": "seller@example.com",
                "pre_auth_token": "pre-auth-token",
                "code": "000000",
            },
            request=None,
            db=None,
        )

    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid verification code"
    assert FakeAsyncClient.post_calls == [
        {
            "url": "https://ai.market.test/api/v1/auth/2fa/verify",
            "json": {"pre_auth_token": "pre-auth-token", "code": "000000"},
        }
    ]
    assert FakeAsyncClient.get_calls == []
    assert mock_ai_market == []
