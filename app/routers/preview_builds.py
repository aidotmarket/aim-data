"""Private builder subrouter mounted by the existing marketplace router."""

import json
from functools import lru_cache
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool
from app.auth.api_key_auth import get_current_user
from app.config import settings
from app.services.processing_service import get_processing_service
from app.services.preview_build_service import PreviewBuildService, BuildError
from app.services.dataset_merkle_service import CommitmentValidationError
from app.services.preview_package_service import PackageError, MEDIA_TYPE
from app.services.preview_content_policy import PolicyError
from app.services.preview_origin_service import OriginError
from app.services.preview_signing_service import SigningError
from app.services.preview_lifecycle import LifecycleError
from app.models.preview_build_schemas import (
    CreateBuild,
    Selection,
    Consent,
    PackageOptions,
    OriginOptions,
    CandidateOptions,
    EmptyOptions,
    MetadataApproval,
)
from app.services.dataset_canonicalization import _pairs

router = APIRouter(prefix="/preview-builds")


@lru_cache(maxsize=1)
def get_build_service():
    from pathlib import Path

    return PreviewBuildService(
        Path(settings.data_directory).resolve() / "preview-builds",
        get_processing_service(),
        settings.upload_directory,
    )


async def authenticated_owner(user=Depends(get_current_user)):
    if not user.is_valid or user.key_id == "mock_key_auth_disabled":
        raise HTTPException(401, "authentication_required")
    if "write" not in user.scopes and "admin" not in user.scopes:
        raise HTTPException(403, "write_scope_required")
    return user.user_id


async def options(request, model):
    # Bounded streaming and non-reflecting errors, including unknown fields.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 262144:
            raise HTTPException(422, "options_limit")
    try:
        return model.model_validate(json.loads(raw or b"{}", object_pairs_hook=_pairs))
    except Exception:
        raise HTTPException(422, "invalid_options") from None


async def invoke(fn, *args):
    try:
        return await run_in_threadpool(fn, *args)
    except BuildError as exc:
        raise HTTPException(exc.status, exc.code) from None
    except (
        CommitmentValidationError,
        PackageError,
        PolicyError,
        OriginError,
        SigningError,
        LifecycleError,
    ) as exc:
        # These services expose only stable non-content codes.
        raise HTTPException(422, str(exc)) from None
    except Exception:
        raise HTTPException(409, "preview_operation_failed") from None


@router.post("")
async def create(
    request: Request,
    owner=Depends(authenticated_owner),
    service=Depends(get_build_service),
):
    return await invoke(service.create, await options(request, CreateBuild), owner)


@router.get("")
async def latest(
    dataset_id: str,
    owner=Depends(authenticated_owner),
    service=Depends(get_build_service),
):
    return await invoke(service.latest, dataset_id, owner)


@router.post("/metadata-approval")
async def metadata_approval(
    request: Request,
    owner=Depends(authenticated_owner),
    service=Depends(get_build_service),
):
    return await invoke(
        service.approve_metadata, await options(request, MetadataApproval), owner
    )


@router.get("/{job_id}")
async def status(
    job_id: str, owner=Depends(authenticated_owner), service=Depends(get_build_service)
):
    return await invoke(service.status, job_id, owner)


@router.get("/{job_id}/rows")
async def rows(
    job_id: str,
    start: int = 0,
    count: int = 25,
    owner=Depends(authenticated_owner),
    service=Depends(get_build_service),
):
    result = await invoke(service.rows, job_id, owner, start, count)
    return Response(
        json.dumps(result),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{job_id}/cancel")
async def cancel(
    job_id: str,
    request: Request,
    owner=Depends(authenticated_owner),
    service=Depends(get_build_service),
):
    await options(request, EmptyOptions)
    return await invoke(service.cancel, job_id, owner)


@router.put("/{job_id}/selection")
async def selection(
    job_id: str,
    request: Request,
    owner=Depends(authenticated_owner),
    service=Depends(get_build_service),
):
    return await invoke(
        service.select, job_id, owner, await options(request, Selection)
    )


@router.post("/{job_id}/policy")
async def policy(
    job_id: str,
    request: Request,
    owner=Depends(authenticated_owner),
    service=Depends(get_build_service),
):
    result = await invoke(service.scan, job_id, owner, await options(request, Consent))
    return result["policy"]


@router.post("/{job_id}/package")
async def package(
    job_id: str,
    request: Request,
    owner=Depends(authenticated_owner),
    service=Depends(get_build_service),
):
    body = await options(request, PackageOptions)
    return await invoke(service.package, job_id, owner, body.destination)


@router.get("/{job_id}/package")
async def download(
    job_id: str, owner=Depends(authenticated_owner), service=Depends(get_build_service)
):
    payload = await invoke(service.download, job_id, owner)
    return Response(
        payload,
        media_type=MEDIA_TYPE,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="preview.json"',
        },
    )


@router.post("/{job_id}/origin-check")
async def origin_check(
    job_id: str,
    request: Request,
    owner=Depends(authenticated_owner),
    service=Depends(get_build_service),
):
    body = await options(request, OriginOptions)
    return await invoke(service.origin_check, job_id, owner, body.url)


@router.post("/{job_id}/candidate")
async def candidate(
    job_id: str,
    request: Request,
    owner=Depends(authenticated_owner),
    service=Depends(get_build_service),
):
    return await invoke(
        service.candidate, job_id, owner, await options(request, CandidateOptions)
    )


@router.post("/{job_id}/submit")
async def submit(
    job_id: str,
    request: Request,
    owner=Depends(authenticated_owner),
    service=Depends(get_build_service),
):
    await options(request, EmptyOptions)
    return await invoke(service.submit, job_id, owner)


@router.post("/{job_id}/withdraw")
async def withdraw(
    job_id: str,
    request: Request,
    owner=Depends(authenticated_owner),
    service=Depends(get_build_service),
):
    await options(request, EmptyOptions)
    return await invoke(service.withdraw, job_id, owner)


@router.post("/{job_id}/refresh")
async def refresh(
    job_id: str,
    request: Request,
    owner=Depends(authenticated_owner),
    service=Depends(get_build_service),
):
    return await invoke(
        service.refresh, job_id, owner, await options(request, CandidateOptions)
    )
