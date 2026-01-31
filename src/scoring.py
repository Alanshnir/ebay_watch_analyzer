from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

KEYWORDS = {
    "not working": 12,
    "for parts": 10,
    "repair": 8,
    "untested": 8,
    "as is": 6,
    "needs battery": 6,
    "unknown": 4,
}


@dataclass
class ScoreResult:
    score: float
    reasons: List[str]


def _normalize_text(*values: str) -> str:
    return " ".join(v.lower() for v in values if v)


def score_item(
    item: Dict,
    min_feedback_pct: float,
    min_feedback_score: int,
) -> ScoreResult:
    reasons: List[str] = []
    score = 0.0

    title = item.get("title", "")
    short_description = item.get("shortDescription", "")
    condition_desc = item.get("conditionDescription", "")
    normalized = _normalize_text(title, short_description, condition_desc)

    for keyword, weight in KEYWORDS.items():
        if keyword in normalized:
            score += weight
            reasons.append(f"keyword:{keyword}")

    condition_id = str(item.get("conditionId") or "")
    if condition_id == "7000":
        score += 10
        reasons.append("condition:for_parts")

    seller = item.get("seller", {})
    feedback_pct = seller.get("feedbackPercentage")
    feedback_score = seller.get("feedbackScore")
    if feedback_pct is not None:
        if feedback_pct >= min_feedback_pct:
            score += 5
            reasons.append("seller:high_feedback_pct")
        else:
            score -= 8
            reasons.append("seller:low_feedback_pct")
    else:
        reasons.append("seller:missing_feedback_pct")

    if feedback_score is not None:
        if feedback_score >= min_feedback_score:
            score += 5
            reasons.append("seller:high_feedback_score")
        else:
            score -= 6
            reasons.append("seller:low_feedback_score")
    else:
        reasons.append("seller:missing_feedback_score")

    return_terms = item.get("returnTerms") or {}
    returns_accepted = return_terms.get("returnsAccepted")
    if returns_accepted is True:
        score += 4
        reasons.append("returns:accepted")
    elif returns_accepted is False:
        score -= 4
        reasons.append("returns:not_accepted")

    price_value, shipping_value, all_in_cost = _extract_price(item)
    if all_in_cost is not None:
        if all_in_cost <= 100:
            score += 4
            reasons.append("price:<=100")
        elif all_in_cost <= 200:
            score += 2
            reasons.append("price:<=200")
        elif all_in_cost > 300:
            score -= 3
            reasons.append("price:>300")

    return ScoreResult(score=round(score, 2), reasons=reasons)


def _extract_price(item: Dict) -> Tuple[float | None, float | None, float | None]:
    price = item.get("price") or {}
    shipping = item.get("shippingOptions") or []
    price_value = _safe_float(price.get("value"))
    shipping_value = None
    if shipping:
        shipping_value = _safe_float(shipping[0].get("shippingCost", {}).get("value"))
    if price_value is None:
        return None, shipping_value, None
    all_in_cost = price_value + (shipping_value or 0.0)
    return price_value, shipping_value, all_in_cost


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_pricing(item: Dict) -> Dict[str, float | None]:
    price_value, shipping_value, all_in_cost = _extract_price(item)
    return {
        "price_value": price_value,
        "shipping_value": shipping_value,
        "all_in_cost": all_in_cost,
    }
