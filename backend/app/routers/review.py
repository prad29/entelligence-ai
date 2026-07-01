from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select
from typing import Optional
from datetime import datetime
import json

from app.database import get_session
from app.models import ReviewItem, AmenityMapping, AuditLog
from app.schemas import ReviewDecision
from app.detection.loader import build_engine_from_db

router = APIRouter(prefix="/api/v1/review", tags=["review"])


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
    item: ReviewItem,
    session: Session,
    request: Request,
) -> None:
    """
    Core approval logic shared between single-approve and bulk-approve.
    Handles both 'mapping' and 'ai_suggestion' review types.
    """
    if item.type == "mapping":
        if item.mapping_id:
            m = session.get(AmenityMapping, item.mapping_id)
            if m:
                before = {"status": m.status}
                m.status = "approved"
                write_audit(
                    session,
                    "amenity_mappings",
                    m.id,
                    "approve",
                    before=before,
                    after={"status": "approved"},
                )

    elif item.type == "ai_suggestion":
        # Create a new approved mapping from the AI suggestion
        payload = {}
        if item.payload:
            try:
                payload = json.loads(item.payload)
            except (json.JSONDecodeError, TypeError):
                payload = {}

        m = AmenityMapping(
            amenity_keyword=item.source_string or payload.get("amenity_keyword", ""),
            screen_format=item.suggested_format or payload.get("screen_format", ""),
            priority_tier=int(payload.get("priority_tier", 4)),
            circuit_name=item.circuit or payload.get("circuit_name"),
            na_default=payload.get("na_default"),
            status="approved",
        )
        session.add(m)
        write_audit(
            session,
            "amenity_mappings",
            0,
            "create_from_ai",
            after={
                "amenity_keyword": m.amenity_keyword,
                "screen_format": m.screen_format,
            },
        )

    elif item.type == "circuit_override":
        from app.models import CircuitOverride
        if item.mapping_id:
            o = session.get(CircuitOverride, item.mapping_id)
            if o:
                before = {"status": o.status}
                o.status = "approved"
                write_audit(
                    session,
                    "circuit_overrides",
                    o.id,
                    "approve",
                    before=before,
                    after={"status": "approved"},
                )

    item.status = "approved"
    item.decided_at = datetime.utcnow()


@router.get("")
def list_review_items(
    type: Optional[str] = None,
    status: Optional[str] = "pending",
    skip: int = 0,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    q = select(ReviewItem)
    if type:
        q = q.where(ReviewItem.type == type)
    if status:
        q = q.where(ReviewItem.status == status)
    q = q.offset(skip).limit(limit)
    return session.exec(q).all()


@router.post("/{id}/approve")
def approve_review_item(
    id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    item = session.get(ReviewItem, id)
    if not item:
        raise HTTPException(404)
    if item.status != "pending":
        raise HTTPException(400, detail=f"Review item is already '{item.status}'")

    _approve_review_item(item, session, request)
    session.commit()
    request.app.state.engine = build_engine_from_db(session)
    return {"ok": True}


@router.post("/{id}/reject")
def reject_review_item(
    id: int,
    body: ReviewDecision,
    request: Request,
    session: Session = Depends(get_session),
):
    item = session.get(ReviewItem, id)
    if not item:
        raise HTTPException(404)
    if item.status != "pending":
        raise HTTPException(400, detail=f"Review item is already '{item.status}'")
    if not body.reason:
        raise HTTPException(400, detail="A reason is required when rejecting")

    item.status = "rejected"
    item.decided_at = datetime.utcnow()

    if item.mapping_id and item.type == "mapping":
        m = session.get(AmenityMapping, item.mapping_id)
        if m:
            before = {"status": m.status}
            m.status = "rejected"
            write_audit(
                session,
                "amenity_mappings",
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
        item = session.get(ReviewItem, item_id)
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
        request.app.state.engine = build_engine_from_db(session)

    return {"approved": approved, "skipped": skipped}
