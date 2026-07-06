import json
import logging
import time
from typing import Optional

import httpx

from app.config import settings
from app.detection.types import BedrockSuggestion

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a theater screen-format classifier. "
    "Use ONLY the provided known formats list — no training-data inference. "
    "Return Standard if nothing matches. Return valid JSON only."
)

_INVOKE_PATH = "/model/{model_id}/invoke"
_LIST_MODELS_PATH = "/foundation-models"


def _base_url() -> str:
    return f"https://bedrock-runtime.{settings.BEDROCK_REGION}.amazonaws.com"


def _control_url() -> str:
    return f"https://bedrock.{settings.BEDROCK_REGION}.amazonaws.com"


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {settings.BEDROCK_API_KEY}"}


_MAX_RETRIES = 3
_BACKOFF_BASE = 1


class BedrockClient:
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
            url = _base_url() + _INVOKE_PATH.format(model_id=settings.BEDROCK_MODEL_ID)

            resp = None
            for attempt in range(_MAX_RETRIES + 1):
                resp = httpx.post(
                    url,
                    headers={**_auth_headers(), "Content-Type": "application/json"},
                    json=body,
                    timeout=10,
                )
                if resp.status_code != 429:
                    break
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** attempt))

            if resp.status_code == 429:
                logger.warning(
                    "bedrock_throttled_after_retries",
                    extra={"amenity": amenity, "retries": _MAX_RETRIES},
                )
                return None

            resp.raise_for_status()
            raw = resp.json()
            # Support Mistral (choices[0].message.content) and legacy outputs[0].text shapes
            text = (raw.get("outputs") or [{}])[0].get("text") or (
                raw.get("choices") or [{}]
            )[0].get("message", {}).get("content", "{}")
            # Strip markdown code fences if present (```json ... ```)
            text = text.strip()
            if text.startswith("```"):
                # Split on first fence opening, take content after it
                parts = text.split("```", 2)
                # parts[0]='', parts[1]='json\n{...}\n', parts[2]=''
                inner = parts[1] if len(parts) >= 2 else text
                if inner.startswith("json"):
                    inner = inner[4:]
                text = inner.strip()
            parsed = json.loads(text)
            return BedrockSuggestion(
                detected_keyword=parsed.get("detected_keyword"),
                suggested_screen_format=parsed.get("suggested_screen_format", "Standard"),
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
            url = _control_url() + _LIST_MODELS_PATH
            resp = httpx.get(
                url,
                headers=_auth_headers(),
                params={"byOutputModality": "TEXT"},
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False


bedrock_client = BedrockClient()
