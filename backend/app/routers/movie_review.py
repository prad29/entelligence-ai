from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select
from typing import Optional
from datetime import datetime
import json

from app.database import get_session
from app.models import AuditLog, MovieFormatMapping, MovieFormatReviewItem
from app.schemas import ReviewDecision
from app.movie_detection.loader import build_movie_format_engine_from_db

router = APIRouter(prefix="/api/v1/movie-review", tags=["movie-review"])


class BulkApproveRequest(BaseModel):
    ids: list[int]


def write_audit(
    session: Session,
    table: str,
    record_id,
    action: str,
    before=None,
    after=None,
    actor=None,
) -> None:
    session.add(
        AuditLog(
            table_name=table,
            record_id=str(record_id),
            action=action,
            before_json=json.dumps(before) if before else None,
            after_json=json.dumps(after) if after else None,
            actor=actor,
        )
    )


def _approve_review_item(
    item: MovieFormatReviewItem,
    session: Session,
    request: Request,
) -> None:
    if item.type == "mapping":
        if item.mapping_id:
            m = session.get(MovieFormatMapping, item.mapping_id)
            if m:
                before = {"status": m.status}
                m.status = "approved"
                write_audit(
                    session,
                    "movie_format_mappings",
                    m.id,
                    "approve",
                    before=before,
                    after={"status": "approved"},
                )

    elif item.type == "ai_suggestion":
        payload = {}
        if item.payload:
            try:
                payload = json.loads(item.payload)
            except (json.JSONDecodeError, TypeError):
                payload = {}

        m = MovieFormatMapping(
            keyword=item.source_string or payload.get("keyword", ""),
            format=item.suggested_format or payload.get("format", "2D"),
            priority_tier=int(payload.get("priority_tier", 4)),
            status="approved",
        )
        session.add(m)
        write_audit(
            session,
            "movie_format_mappings",
            0,
            "create_from_ai",
            after={"keyword": m.keyword, "format": m.format},
        )

        try:
            from app.cache import get_redis, movie_format_cache_key
            get_redis().delete(movie_format_cache_key(item.source_string or ""))
        except Exception:
            pass

    item.status = "approved"
    item.decided_at = datetime.utcnow()


@router.get("")
def list_review_items(
    type: Optional[str] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    q = select(MovieFormatReviewItem)
    if type:
        q = q.where(MovieFormatReviewItem.type == type)
    if status:
        q = q.where(MovieFormatReviewItem.status == status)
    q = q.offset(skip).limit(limit)
    return session.exec(q).all()


@router.post("/{id}/approve")
def approve_review_item(
    id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    item = session.get(MovieFormatReviewItem, id)
    if not item:
        raise HTTPException(404)
    if item.status != "pending":
        raise HTTPException(400, detail=f"Review item is already '{item.status}'")

    _approve_review_item(item, session, request)
    session.commit()
    request.app.state.movie_engine = build_movie_format_engine_from_db(session)
    return {"ok": True}


@router.post("/{id}/reject")
def reject_review_item(
    id: int,
    body: ReviewDecision,
    request: Request,
    session: Session = Depends(get_session),
):
    item = session.get(MovieFormatReviewItem, id)
    if not item:
        raise HTTPException(404)
    if item.status != "pending":
        raise HTTPException(400, detail=f"Review item is already '{item.status}'")

    item.status = "rejected"
    item.decided_at = datetime.utcnow()

    if item.mapping_id and item.type == "mapping":
        m = session.get(MovieFormatMapping, item.mapping_id)
        if m:
            before = {"status": m.status}
            m.status = "rejected"
            write_audit(
                session,
                "movie_format_mappings",
                m.id,
                "reject",
                before=before,
                after={"status": "rejected", "reason": body.reason},
            )

    session.commit()
    return {"ok": True}


@router.post("/bulk-approve")
def bulk_approve(
    body: BulkApproveRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    if not body.ids:
        raise HTTPException(400, detail="No ids provided")

    approved = []
    skipped = []
    for item_id in body.ids:
        item = session.get(MovieFormatReviewItem, item_id)
        if not item:
            skipped.append({"id": item_id, "reason": "not_found"})
            continue
        if item.status != "pending":
            skipped.append({"id": item_id, "reason": f"already_{item.status}"})
            continue
        _approve_review_item(item, session, request)
        approved.append(item_id)

    session.commit()
    if approved:
        request.app.state.movie_engine = build_movie_format_engine_from_db(session)

    return {"approved": approved, "skipped": skipped}
