import boto3
import json
import logging
from typing import Optional

from app.detection.types import BedrockSuggestion
from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a theater screen-format classifier. "
    "Use ONLY the provided known formats list — no training-data inference. "
    "Return Standard if nothing matches. Return valid JSON only."
)


class BedrockClient:
    _client = None

    def _get_runtime(self):
        if self._client is None:
            self._client = boto3.client(
                "bedrock-runtime", region_name=settings.BEDROCK_REGION
            )
        return self._client

    def classify_single(
        self, amenity: str, circuit: str, known_formats: list
    ) -> Optional[BedrockSuggestion]:
        try:
            prompt = (
                f'Amenity: "{amenity}"\nCircuit: "{circuit or "unknown"}"\n'
                "Known formats:\n"
                + "\n".join(f"- {f}" for f in known_formats)
                + '\n\nReturn ONLY JSON: {"detected_keyword": null_or_str, "suggested_screen_format": str, "confidence": 0.0-1.0, "reasoning": str}'
            )
            body = {
                "messages": [{"role": "user", "content": prompt}],
                "system": SYSTEM_PROMPT,
                "max_tokens": 256,
                "temperature": 0,
            }
            resp = self._get_runtime().invoke_model(
                modelId=settings.BEDROCK_MODEL_ID,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            raw = json.loads(resp["body"].read())
            # Support Mistral and generic message-based response shapes
            text = (raw.get("outputs") or [{}])[0].get("text") or (
                raw.get("choices") or [{}]
            )[0].get("message", {}).get("content", "{}")
            parsed = json.loads(text)
            return BedrockSuggestion(
                detected_keyword=parsed.get("detected_keyword"),
                suggested_screen_format=parsed.get(
                    "suggested_screen_format", "Standard"
                ),
                confidence=float(parsed.get("confidence", 0.5)),
                reasoning=parsed.get("reasoning", ""),
            )
        except Exception as e:
            logger.error(
                "bedrock_classify_single_error",
                extra={"error": str(e), "amenity": amenity},
            )
            return None

    def check_connection(self) -> bool:
        try:
            boto3.client(
                "bedrock", region_name=settings.BEDROCK_REGION
            ).list_foundation_models(byOutputModality="TEXT")
            return True
        except Exception:
            return False


bedrock_client = BedrockClient()
