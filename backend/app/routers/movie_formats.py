from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select, func
from typing import Optional
import io
import json
import math

import openpyxl

from app.database import get_session
from app.models import AuditLog, MovieFormatMapping, MovieFormatReviewItem
from app.schemas import MovieFormatMappingCreate, MovieFormatMappingPatch, ReviewDecision, PaginatedResponse
from app.movie_detection.loader import build_movie_format_engine_from_db

router = APIRouter(prefix="/api/v1/movie-formats", tags=["movie-formats"])


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


@router.get("")
def list_movie_formats(
    search: Optional[str] = None,
    status: Optional[str] = None,
    tier: Optional[str] = None,
    format: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    session: Session = Depends(get_session),
):
    q = select(MovieFormatMapping)
    if search:
        q = q.where(MovieFormatMapping.keyword.contains(search))
    if status:
        q = q.where(MovieFormatMapping.status == status)
    if tier:
        tier_int = int(tier.lstrip("P"))
        q = q.where(MovieFormatMapping.priority_tier == tier_int)
    if format:
        q = q.where(MovieFormatMapping.format == format)

    count_q = select(func.count()).select_from(q.subquery())
    total = session.exec(count_q).one()

    q = q.order_by(MovieFormatMapping.priority_tier, MovieFormatMapping.keyword)
    items = session.exec(q.offset((page - 1) * page_size).limit(page_size)).all()
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, math.ceil(total / page_size)),
    )


@router.post("")
def create_movie_format(
    data: MovieFormatMappingCreate,
    request: Request,
    session: Session = Depends(get_session),
):
    incoming = data.dict()
    status = incoming.get("status") if incoming.get("status") == "approved" else "pending"
    m = MovieFormatMapping(**incoming, status=status)
    session.add(m)
    write_audit(session, "movie_format_mappings", m.id, "create", after=incoming)
    session.commit()
    session.refresh(m)
    if m.status == "approved":
        request.app.state.movie_engine = build_movie_format_engine_from_db(session)
    return m


@router.put("/{id}")
def update_movie_format(
    id: int,
    data: MovieFormatMappingCreate,
    request: Request,
    session: Session = Depends(get_session),
):
    m = session.get(MovieFormatMapping, id)
    if not m:
        raise HTTPException(404)
    before = m.dict()
    incoming = data.dict()
    for k, v in incoming.items():
        setattr(m, k, v)
    if incoming.get("status") != "approved":
        m.status = "pending"
    m.version += 1
    write_audit(session, "movie_format_mappings", id, "update", before=before, after=incoming)
    session.commit()
    session.refresh(m)
    if m.status == "approved":
        request.app.state.movie_engine = build_movie_format_engine_from_db(session)
    return m


@router.patch("/{id}")
def patch_movie_format(
    id: int,
    data: MovieFormatMappingPatch,
    request: Request,
    session: Session = Depends(get_session),
):
    m = session.get(MovieFormatMapping, id)
    if not m:
        raise HTTPException(404)
    before = m.dict()
    patch_data = data.dict(exclude_unset=True)
    for k, v in patch_data.items():
        setattr(m, k, v)
    if patch_data.get("status") != "approved":
        m.status = "pending"
    m.version += 1
    write_audit(session, "movie_format_mappings", id, "patch", before=before, after=patch_data)
    session.commit()
    session.refresh(m)
    if m.status == "approved":
        request.app.state.movie_engine = build_movie_format_engine_from_db(session)
    return m


@router.delete("/{id}")
def delete_movie_format(
    id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    m = session.get(MovieFormatMapping, id)
    if not m:
        raise HTTPException(404)
    write_audit(session, "movie_format_mappings", id, "delete", before=m.dict())
    session.delete(m)
    session.commit()
    request.app.state.movie_engine = build_movie_format_engine_from_db(session)
    return {"ok": True}


@router.post("/{id}/approve")
def approve_movie_format(
    id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    m = session.get(MovieFormatMapping, id)
    if not m:
        raise HTTPException(404)
    before = {"status": m.status}
    m.status = "approved"
    write_audit(
        session,
        "movie_format_mappings",
        id,
        "approve",
        before=before,
        after={"status": "approved"},
    )
    session.commit()
    request.app.state.movie_engine = build_movie_format_engine_from_db(session)
    return {"ok": True}


@router.post("/{id}/reject")
def reject_movie_format(
    id: int,
    body: ReviewDecision,
    request: Request,
    session: Session = Depends(get_session),
):
    m = session.get(MovieFormatMapping, id)
    if not m:
        raise HTTPException(404)
    before = {"status": m.status}
    m.status = "rejected"
    write_audit(
        session,
        "movie_format_mappings",
        id,
        "reject",
        before=before,
        after={"status": "rejected", "reason": body.reason},
    )
    session.commit()
    return {"ok": True}


@router.get("/export")
def export_xlsx(session: Session = Depends(get_session)):
    mappings = session.exec(
        select(MovieFormatMapping).where(MovieFormatMapping.status == "approved")
    ).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["keyword", "format", "priority_tier", "status"])
    for m in mappings:
        ws.append([m.keyword, m.format, m.priority_tier, m.status])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=movie_formats_export.xlsx"},
    )
