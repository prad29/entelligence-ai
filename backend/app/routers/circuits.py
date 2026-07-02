from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select
from typing import Optional
import json

from app.database import get_session
from app.models import CircuitOverride, CircuitAlias, AuditLog, ReviewItem
from app.schemas import CircuitOverrideCreate, CircuitAliasCreate, ReviewDecision
from app.detection.loader import build_engine_from_db

router = APIRouter(prefix="/api/v1/circuits", tags=["circuits"])


def _json_safe(obj):
    return json.dumps(obj, default=str)


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
            before_json=_json_safe(before) if before else None,
            after_json=_json_safe(after) if after else None,
            actor=actor,
        )
    )


# ──────────────────────────────────────────────
# Circuit Overrides
# ──────────────────────────────────────────────

@router.get("/overrides")
def list_overrides(
    circuit: Optional[str] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    q = select(CircuitOverride)
    if circuit:
        q = q.where(CircuitOverride.circuit_name == circuit)
    if status:
        q = q.where(CircuitOverride.status == status)
    q = q.offset(skip).limit(limit)
    return session.exec(q).all()


@router.post("/overrides")
def create_override(
    data: CircuitOverrideCreate,
    request: Request,
    session: Session = Depends(get_session),
):
    o = CircuitOverride(**data.dict(), status="pending")
    session.add(o)
    session.flush()
    session.add(
        ReviewItem(
            type="circuit_override",
            source_string=o.keyword,
            circuit=o.circuit_name,
            suggested_format=o.screen_format,
        )
    )
    write_audit(session, "circuit_overrides", o.id, "create", after=data.dict())
    session.commit()
    session.refresh(o)
    return o


@router.put("/overrides/{id}")
def update_override(
    id: int,
    data: CircuitOverrideCreate,
    request: Request,
    session: Session = Depends(get_session),
):
    o = session.get(CircuitOverride, id)
    if not o:
        raise HTTPException(404)
    before = o.dict()
    for k, v in data.dict().items():
        setattr(o, k, v)
    o.status = "pending"
    session.add(
        ReviewItem(
            type="circuit_override",
            source_string=o.keyword,
            circuit=o.circuit_name,
            suggested_format=o.screen_format,
        )
    )
    write_audit(session, "circuit_overrides", id, "update", before=before, after=data.dict())
    session.commit()
    session.refresh(o)
    return o


@router.post("/overrides/{id}/approve")
def approve_override(
    id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    o = session.get(CircuitOverride, id)
    if not o:
        raise HTTPException(404)
    before = {"status": o.status}
    o.status = "approved"
    write_audit(
        session,
        "circuit_overrides",
        id,
        "approve",
        before=before,
        after={"status": "approved"},
    )
    session.commit()
    request.app.state.engine = build_engine_from_db(session)
    return {"ok": True}


@router.post("/overrides/{id}/reject")
def reject_override(
    id: int,
    body: ReviewDecision,
    request: Request,
    session: Session = Depends(get_session),
):
    o = session.get(CircuitOverride, id)
    if not o:
        raise HTTPException(404)
    before = {"status": o.status}
    o.status = "rejected"
    write_audit(
        session,
        "circuit_overrides",
        id,
        "reject",
        before=before,
        after={"status": "rejected", "reason": body.reason},
    )
    session.commit()
    return {"ok": True}


@router.delete("/overrides/{id}")
def delete_override(
    id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    o = session.get(CircuitOverride, id)
    if not o:
        raise HTTPException(404)
    before = o.dict()
    session.delete(o)
    write_audit(session, "circuit_overrides", id, "delete", before=before)
    session.commit()
    request.app.state.engine = build_engine_from_db(session)
    return {"ok": True}


# ──────────────────────────────────────────────
# Circuit Aliases
# ──────────────────────────────────────────────

@router.get("/aliases")
def list_aliases(
    skip: int = 0,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    q = select(CircuitAlias).offset(skip).limit(limit)
    return session.exec(q).all()


@router.post("/aliases")
def create_alias(
    data: CircuitAliasCreate,
    request: Request,
    session: Session = Depends(get_session),
):
    existing = session.exec(
        select(CircuitAlias).where(CircuitAlias.raw_or_alias == data.raw_or_alias)
    ).first()
    if existing:
        raise HTTPException(409, detail="Alias already exists")
    a = CircuitAlias(**data.dict())
    session.add(a)
    write_audit(session, "circuit_aliases", data.raw_or_alias, "create", after=data.dict())
    session.commit()
    session.refresh(a)
    request.app.state.engine = build_engine_from_db(session)
    return a


@router.put("/aliases/{id}")
def update_alias(
    id: int,
    data: CircuitAliasCreate,
    request: Request,
    session: Session = Depends(get_session),
):
    a = session.get(CircuitAlias, id)
    if not a:
        raise HTTPException(404)
    before = a.dict()
    a.raw_or_alias = data.raw_or_alias
    a.canonical = data.canonical
    write_audit(session, "circuit_aliases", id, "update", before=before, after=data.dict())
    session.commit()
    session.refresh(a)
    request.app.state.engine = build_engine_from_db(session)
    return a


@router.delete("/aliases/{id}")
def delete_alias(
    id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    a = session.get(CircuitAlias, id)
    if not a:
        raise HTTPException(404)
    before = a.dict()
    session.delete(a)
    write_audit(session, "circuit_aliases", id, "delete", before=before)
    session.commit()
    request.app.state.engine = build_engine_from_db(session)
    return {"ok": True}
