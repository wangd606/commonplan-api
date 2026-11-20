from fastapi import APIRouter, HTTPException, status

from app.auth import CurrentUser
from app.schemas import (
    GroundedAnswerRequest,
    GroundedAnswerResponse,
    HybridRetrievalSearchRequest,
    HybridRetrievalSearchResponse,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from app.services.retrieval_service import (
    RetrievalForbidden,
    RetrievalNotFound,
    RetrievalServiceDep,
    RetrievalUnavailable,
)


router = APIRouter(prefix="/api/v1", tags=["retrieval"])


@router.post(
    "/workspaces/{workspace_id}/retrieval/search",
    response_model=RetrievalSearchResponse,
)
def search_knowledge(
    workspace_id: str,
    payload: RetrievalSearchRequest,
    current_user: CurrentUser,
    service: RetrievalServiceDep,
):
    try:
        return service.search(
            current_user,
            workspace_id,
            query=payload.query,
            mode=payload.mode,
            team_ids=payload.team_ids,
            project_id=payload.project_id,
            issue_id=payload.issue_id,
            limit=payload.limit,
        )
    except RetrievalNotFound as exc:
        raise HTTPException(status_code=404, detail="Retrieval scope not found") from exc
    except RetrievalForbidden as exc:
        raise HTTPException(status_code=403, detail="Insufficient retrieval access") from exc
    except RetrievalUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Retrieval model is unavailable",
        ) from exc


@router.post(
    "/workspaces/{workspace_id}/retrieval/hybrid-search",
    response_model=HybridRetrievalSearchResponse,
)
def hybrid_search_knowledge(
    workspace_id: str,
    payload: HybridRetrievalSearchRequest,
    current_user: CurrentUser,
    service: RetrievalServiceDep,
):
    try:
        return service.hybrid_search(
            current_user,
            workspace_id,
            **payload.model_dump(),
        )
    except RetrievalNotFound as exc:
        raise HTTPException(status_code=404, detail="Retrieval scope not found") from exc
    except RetrievalForbidden as exc:
        raise HTTPException(status_code=403, detail="Insufficient retrieval access") from exc
    except RetrievalUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Retrieval model is unavailable",
        ) from exc


@router.post(
    "/workspaces/{workspace_id}/retrieval/answer",
    response_model=GroundedAnswerResponse,
)
def answer_from_knowledge(
    workspace_id: str,
    payload: GroundedAnswerRequest,
    current_user: CurrentUser,
    service: RetrievalServiceDep,
):
    try:
        return service.answer(
            current_user,
            workspace_id,
            **payload.model_dump(),
        )
    except RetrievalNotFound as exc:
        raise HTTPException(status_code=404, detail="Retrieval scope not found") from exc
    except RetrievalForbidden as exc:
        raise HTTPException(status_code=403, detail="Insufficient retrieval access") from exc
    except RetrievalUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Grounded answer generation is unavailable",
        ) from exc
