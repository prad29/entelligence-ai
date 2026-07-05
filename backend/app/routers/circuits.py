from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select
import json

from app.database import get_session
from app.models import CircuitAlias, AuditLog
from app.schemas import CircuitAliasCreate
from app.detection.loader import build_engine_from_db

router = APIRouter(prefix="/api/v1/circuits", tags=["circuits"])


def _json_safe(obj):
    return json.dumps(obj, default=str)


def write_audit(session, table, record_id, action, before=None, after=None):
    session.add(AuditLog(
        table_name=table,
        record_id=str(record_id),
        action=action,
        before_json=_json_safe(before) if before else None,
        after_json=_json_safe(after) if after else None,
    ))


@router.get("/aliases")
def list_aliases(skip: int = 0, limit: int = 200, session: Session = Depends(get_session)):
    return session.exec(select(CircuitAlias).offset(skip).limit(limit)).all()


@router.post("/aliases")
def create_alias(data: CircuitAliasCreate, request: Request, session: Session = Depends(get_session)):
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
def update_alias(id: int, data: CircuitAliasCreate, request: Request, session: Session = Depends(get_session)):
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
def delete_alias(id: int, request: Request, session: Session = Depends(get_session)):
    a = session.get(CircuitAlias, id)
    if not a:
        raise HTTPException(404)
    before = a.dict()
    session.delete(a)
    write_audit(session, "circuit_aliases", id, "delete", before=before)
    session.commit()
    request.app.state.engine = build_engine_from_db(session)
    return {"ok": True}
