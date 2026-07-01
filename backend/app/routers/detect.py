from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/v1/detect", tags=["detect"])


class DetectSingleRequest(BaseModel):
    amenity: str
    circuit_name: Optional[str] = ""


@router.post("/single")
async def detect_single(payload: DetectSingleRequest, request: Request):
    engine = request.app.state.engine
    result = engine.detect(payload.amenity, payload.circuit_name or "")
    return result.__dict__ if hasattr(result, "__dict__") else result
