from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


@router.get("/bedrock/status")
def bedrock_status():
    from app.config import settings

    try:
        import boto3

        client = boto3.client("bedrock", region_name=settings.BEDROCK_REGION)
        client.list_foundation_models(byOutputModality="TEXT")
        return {
            "connected": True,
            "model_id": settings.BEDROCK_MODEL_ID,
            "region": settings.BEDROCK_REGION,
        }
    except Exception as e:
        return {
            "connected": False,
            "model_id": settings.BEDROCK_MODEL_ID,
            "region": settings.BEDROCK_REGION,
            "error": str(e),
        }


@router.get("")
def get_settings():
    from app.config import settings

    return {
        "bedrock_model_id": settings.BEDROCK_MODEL_ID,
        "bedrock_region": settings.BEDROCK_REGION,
        "ai_trigger_mode": settings.AI_TRIGGER_MODE,
        "max_batch_rows": settings.MAX_BATCH_ROWS,
        "job_ttl_hours": settings.JOB_TTL_HOURS,
    }
