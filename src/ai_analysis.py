import json
import logging
import os
import time
from typing import Any, Dict, List

import requests
from requests import exceptions as req_exc


class AiAnalysisError(RuntimeError):
    pass


_LAST_AI_REQUEST_TS = 0.0


AI_RESULT_KEYS = {
    "ai_provider",
    "ai_model",
    "ai_flip_candidate",
    "ai_equivalent_sale_price",
    "ai_sell_ease",
    "ai_needed_parts",
    "ai_parts_cost_estimate",
    "ai_confidence",
    "ai_summary",
    "ai_estimated_profit",
    "ai_error",
}


def analyze_listing(item: Dict[str, Any], candidate_row: Dict[str, Any]) -> Dict[str, Any]:
    provider = (os.getenv("AI_PROVIDER") or "").strip().lower()
    if not provider:
        return _empty_result("disabled", "AI_PROVIDER not configured")

    _apply_rate_limit()

    if provider == "openai":
        return _analyze_with_openai(item, candidate_row)
    if provider == "gemini":
        return _analyze_with_gemini(item, candidate_row)

    return _empty_result(provider, f"Unsupported AI_PROVIDER={provider}")


def analyze_gemini_bulk(rows_with_items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Single-request Gemini analysis for all candidate rows.
    Returns mapping: itemId -> ai_result_dict
    """
    provider = (os.getenv("AI_PROVIDER") or "").strip().lower()
    if provider != "gemini":
        raise AiAnalysisError("Bulk Gemini analysis requires AI_PROVIDER=gemini")

    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
    if not api_key:
        raise AiAnalysisError("GEMINI_API_KEY missing")

    _apply_rate_limit()

    listings_payload: List[Dict[str, Any]] = []
    for entry in rows_with_items:
        row = entry.get("row", {})
        item = entry.get("item", {})
        listings_payload.append(
            {
                "itemId": row.get("itemId"),
                "title": row.get("title"),
                "condition": row.get("condition"),
                "conditionId": row.get("conditionId"),
                "all_in_cost": row.get("all_in_cost"),
                "currency": row.get("currency"),
                "seller_feedback_pct": row.get("seller_feedback_pct"),
                "seller_feedback_score": row.get("seller_feedback_score"),
                "returns_accepted": row.get("returns_accepted"),
                "score_total": row.get("score_total"),
                "shortDescription": item.get("shortDescription"),
                "conditionDescription": item.get("conditionDescription"),
                "image_url": row.get("image_url"),
                "itemWebUrl": row.get("itemWebUrl"),
            }
        )

    prompt = _bulk_analysis_prompt(listings_payload)
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}

    response = _request_with_backoff(
        "post",
        url,
        timeout=int(os.getenv("AI_REQUEST_TIMEOUT_SEC", "120")),
        headers=headers,
        json=body,
    )
    data = response.json()
    text = _extract_gemini_text(data)
    parsed = _parse_json(text)

    analyses = parsed.get("analyses") if isinstance(parsed, dict) else None
    if not isinstance(analyses, list):
        raise AiAnalysisError("Gemini bulk response missing 'analyses' array")

    result_by_item: Dict[str, Dict[str, Any]] = {}
    for entry in analyses:
        if not isinstance(entry, dict):
            continue
        item_id = str(entry.get("itemId") or "").strip()
        if not item_id:
            continue
        normalized = _normalize_analysis(
            entry,
            provider="gemini",
            model=model,
            all_in_cost=_safe_float(entry.get("all_in_cost")) or 0.0,
        )
        # preserve explicit model-side errors if returned per row
        if entry.get("ai_error") and not normalized.get("ai_error"):
            normalized["ai_error"] = str(entry.get("ai_error"))
        result_by_item[item_id] = normalized

    return result_by_item


def _bulk_analysis_prompt(listings_payload: List[Dict[str, Any]]) -> str:
    return (
        "You are a watch flipping analyst. Analyze ALL listings in the JSON array. "
        "Return strict JSON only in this shape: "
        "{\"analyses\":[{" 
        "\"itemId\":\"...\","
        "\"flip_candidate\":true|false,"
        "\"equivalent_sale_price\":number,"
        "\"sell_ease\":\"high|medium|low\","
        "\"needed_parts\":[\"...\"],"
        "\"parts_cost_estimate\":number,"
        "\"confidence\":number,"
        "\"summary\":\"...\","
        "\"all_in_cost\":number"
        "}]} "
        "Do not omit any itemId from input. Be concise and conservative.\n\n"
        f"LISTINGS_JSON:\n{json.dumps(listings_payload)}"
    )


def _apply_rate_limit() -> None:
    global _LAST_AI_REQUEST_TS

    rpm = int(os.getenv("AI_REQUESTS_PER_MINUTE", "5"))
    if rpm <= 0:
        rpm = 5

    min_interval = 60.0 / rpm
    now = time.time()
    elapsed = now - _LAST_AI_REQUEST_TS
    if _LAST_AI_REQUEST_TS > 0 and elapsed < min_interval:
        sleep_for = min_interval - elapsed
        logging.info("AI rate limit pacing: sleeping %.2fs", sleep_for)
        time.sleep(sleep_for)
    _LAST_AI_REQUEST_TS = time.time()


def _listing_payload(item: Dict[str, Any], candidate_row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "itemId": item.get("itemId"),
        "title": item.get("title"),
        "shortDescription": item.get("shortDescription"),
        "condition": item.get("condition"),
        "conditionId": item.get("conditionId"),
        "conditionDescription": item.get("conditionDescription"),
        "price": item.get("price"),
        "shippingOptions": item.get("shippingOptions"),
        "seller": item.get("seller"),
        "itemWebUrl": item.get("itemWebUrl"),
        "buyingOptions": item.get("buyingOptions"),
        "images": _image_urls(item),
        "all_in_cost": candidate_row.get("all_in_cost"),
        "currency": candidate_row.get("currency"),
    }


def _image_urls(item: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    image = item.get("image") or {}
    if image.get("imageUrl"):
        urls.append(str(image["imageUrl"]))
    for extra in item.get("additionalImages") or []:
        url = extra.get("imageUrl")
        if url:
            urls.append(str(url))
    deduped = list(dict.fromkeys(urls))
    return deduped[:5]


def _analysis_prompt(payload: Dict[str, Any]) -> str:
    return (
        "You are a watch flipping analyst. Given this eBay listing JSON, estimate if it is a good flip. "
        "Use listing text and image URLs as inputs. Respond with strict JSON only with keys: "
        "flip_candidate (boolean), equivalent_sale_price (number), sell_ease (one of: high|medium|low), "
        "needed_parts (array of strings), parts_cost_estimate (number), confidence (0-1 number), summary (string). "
        "Base equivalent_sale_price on likely sold comps for an equivalent working watch, conservative estimate. "
        "If uncertain, lower confidence and explain in summary.\n\n"
        f"LISTING_JSON:\n{json.dumps(payload)}"
    )


def _analyze_with_openai(item: Dict[str, Any], candidate_row: Dict[str, Any]) -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    if not api_key:
        return _empty_result("openai", "OPENAI_API_KEY missing")

    payload = _listing_payload(item, candidate_row)
    text_prompt = _analysis_prompt(payload)
    content: List[Dict[str, Any]] = [{"type": "input_text", "text": text_prompt}]
    for image_url in payload.get("images", []):
        content.append({"type": "input_image", "image_url": image_url})

    body = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": content,
            }
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = _request_with_backoff(
        "post",
        "https://api.openai.com/v1/responses",
        timeout=int(os.getenv("AI_REQUEST_TIMEOUT_SEC", "60")),
        headers=headers,
        json=body,
    )
    data = response.json()
    text = _extract_openai_text(data)
    parsed = _parse_json(text)
    return _normalize_analysis(
        parsed,
        provider="openai",
        model=model,
        all_in_cost=_safe_float(candidate_row.get("all_in_cost")) or 0.0,
    )


def _analyze_with_gemini(item: Dict[str, Any], candidate_row: Dict[str, Any]) -> Dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
    if not api_key:
        return _empty_result("gemini", "GEMINI_API_KEY missing")

    payload = _listing_payload(item, candidate_row)
    prompt = _analysis_prompt(payload)
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}

    response = _request_with_backoff(
        "post",
        url,
        timeout=int(os.getenv("AI_REQUEST_TIMEOUT_SEC", "60")),
        headers=headers,
        json=body,
    )
    data = response.json()
    text = _extract_gemini_text(data)
    parsed = _parse_json(text)
    return _normalize_analysis(
        parsed,
        provider="gemini",
        model=model,
        all_in_cost=_safe_float(candidate_row.get("all_in_cost")) or 0.0,
    )


def _extract_openai_text(payload: Dict[str, Any]) -> str:
    output = payload.get("output") or []
    collected: List[str] = []
    for block in output:
        for content in block.get("content", []):
            if content.get("type") in {"output_text", "text"}:
                if content.get("text"):
                    collected.append(content["text"])
    if not collected and payload.get("output_text"):
        collected.append(payload["output_text"])
    return "\n".join(collected).strip()


def _extract_gemini_text(payload: Dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return "\n".join(str(p.get("text", "")) for p in parts).strip()


def _parse_json(raw_text: str) -> Dict[str, Any]:
    if not raw_text:
        return {}
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("json", "", 1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logging.warning("AI response was not valid JSON: %s", raw_text[:300])
        return {}


def _normalize_analysis(parsed: Dict[str, Any], provider: str, model: str, all_in_cost: float) -> Dict[str, Any]:
    equivalent_sale_price = _safe_float(parsed.get("equivalent_sale_price"))
    parts_cost_estimate = _safe_float(parsed.get("parts_cost_estimate"))
    estimated_profit = None
    if equivalent_sale_price is not None:
        estimated_profit = equivalent_sale_price - all_in_cost - (parts_cost_estimate or 0.0)

    needed_parts = parsed.get("needed_parts")
    if not isinstance(needed_parts, list):
        needed_parts = []

    return {
        "ai_provider": provider,
        "ai_model": model,
        "ai_flip_candidate": bool(parsed.get("flip_candidate")) if parsed else None,
        "ai_equivalent_sale_price": equivalent_sale_price,
        "ai_sell_ease": parsed.get("sell_ease"),
        "ai_needed_parts": ";".join(str(x) for x in needed_parts if x),
        "ai_parts_cost_estimate": parts_cost_estimate,
        "ai_confidence": _safe_float(parsed.get("confidence")),
        "ai_summary": parsed.get("summary"),
        "ai_estimated_profit": estimated_profit,
        "ai_error": None,
    }


def _empty_result(provider: str, reason: str) -> Dict[str, Any]:
    return {
        "ai_provider": provider,
        "ai_model": None,
        "ai_flip_candidate": None,
        "ai_equivalent_sale_price": None,
        "ai_sell_ease": None,
        "ai_needed_parts": None,
        "ai_parts_cost_estimate": None,
        "ai_confidence": None,
        "ai_summary": None,
        "ai_estimated_profit": None,
        "ai_error": reason,
    }


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _request_with_backoff(method: str, url: str, timeout: int = 60, **kwargs: Any) -> requests.Response:
    retries = 6
    base_delay = 2.0
    retriable_statuses = {429, 500, 502, 503, 504}

    for attempt in range(retries):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
        except (req_exc.Timeout, req_exc.ConnectionError, req_exc.ChunkedEncodingError) as exc:
            delay = base_delay * (2**attempt)
            logging.warning("AI request network error (%s). Retrying in %.1fs", exc.__class__.__name__, delay)
            time.sleep(delay)
            continue

        if response.status_code in retriable_statuses:
            delay = base_delay * (2**attempt)
            logging.warning("AI temporary HTTP %s. Retrying in %.1fs", response.status_code, delay)
            time.sleep(delay)
            continue
        if response.status_code >= 400:
            raise AiAnalysisError(f"AI HTTP {response.status_code}: {response.text[:500]}")
        return response

    raise AiAnalysisError(f"AI retries exceeded for {url}")
