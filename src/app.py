import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set

import pandas as pd
from dotenv import load_dotenv

from .ebay_api import EbayApi
from .scoring import ScoreResult, extract_pricing, score_item
from .storage import init_db, is_seen, mark_seen


CATEGORY_WRISTWATCHES = "31387"


def build_queries(default_queries: List[str]) -> List[str]:
    env_queries = os.getenv("RUN_QUERIES")
    if env_queries:
        return [q.strip() for q in env_queries.split(",") if q.strip()]
    return default_queries


def init_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(),
        ],
    )


def extract_returns_accepted(item: Dict[str, Any]) -> bool | None:
    return_terms = item.get("returnTerms") or {}
    returns_accepted = return_terms.get("returnsAccepted")
    if isinstance(returns_accepted, bool):
        return returns_accepted
    return None


def build_candidate_row(
    item: Dict[str, Any],
    score_result: ScoreResult,
    run_timestamp: str,
) -> Dict[str, Any]:
    seller = item.get("seller", {})
    pricing = extract_pricing(item)
    buying_options = item.get("buyingOptions") or []
    image = item.get("image") or {}

    return {
        "run_timestamp": run_timestamp,
        "itemId": item.get("itemId"),
        "title": item.get("title"),
        "itemWebUrl": item.get("itemWebUrl"),
        "price_value": pricing.get("price_value"),
        "shipping_value": pricing.get("shipping_value"),
        "all_in_cost": pricing.get("all_in_cost"),
        "currency": (item.get("price") or {}).get("currency"),
        "condition": item.get("condition"),
        "conditionId": item.get("conditionId"),
        "listingType": item.get("listingType"),
        "buyingOptions": "|".join(buying_options),
        "image_url": image.get("imageUrl"),
        "seller_username": seller.get("username"),
        "seller_feedback_pct": seller.get("feedbackPercentage"),
        "seller_feedback_score": seller.get("feedbackScore"),
        "returns_accepted": extract_returns_accepted(item),
        "score_total": score_result.score,
        "score_reasons": ";".join(score_result.reasons),
    }


def fetch_items(api: EbayApi, queries: List[str], filters: str, limit: int) -> List[Dict[str, Any]]:
    seen_ids: Set[str] = set()
    items: List[Dict[str, Any]] = []
    for query in queries:
        logging.info("Searching query '%s'", query)
        payload = api.search_items(
            q=query,
            category_ids=CATEGORY_WRISTWATCHES,
            filters=filters,
            limit=limit,
            offset=0,
            sort="newlyListed",
        )
        for summary in payload.get("itemSummaries", []):
            item_id = summary.get("itemId")
            if not item_id or item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            items.append(summary)
    return items


def main() -> None:
    load_dotenv()
    data_dir = Path("data")
    log_path = data_dir / "run.log"
    init_logging(log_path)

    client_id = os.getenv("EBAY_CLIENT_ID")
    client_secret = os.getenv("EBAY_CLIENT_SECRET")
    marketplace_id = os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US")

    if not client_id or not client_secret:
        raise RuntimeError("Missing EBAY_CLIENT_ID or EBAY_CLIENT_SECRET in environment")

    max_price = float(os.getenv("MAX_PRICE", "300"))
    min_feedback_pct = float(os.getenv("MIN_FEEDBACK_PCT", "97.5"))
    min_feedback_score = int(os.getenv("MIN_FEEDBACK_SCORE", "50"))

    api = EbayApi(client_id, client_secret, marketplace_id)
    run_timestamp = datetime.now(timezone.utc).isoformat()

    db_path = data_dir / "seen_items.db"
    init_db(db_path)

    queries = build_queries(
        [
            "wristwatch",
            "watch",
            "repair watch",
            "for parts watch",
            "watch needs battery",
            "watch untested",
        ]
    )

    filters = (
        "conditionIds:{7000},"
        "buyingOptions:{FIXED_PRICE|BEST_OFFER},"
        "deliveryCountry:US,"
        f"price:[..{max_price}]"
    )

    summaries = fetch_items(api, queries, filters, limit=50)
    logging.info("Found %d summary items", len(summaries))

    candidates: List[Dict[str, Any]] = []
    raw_path = data_dir / "raw.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    with raw_path.open("a", encoding="utf-8") as raw_file:
        for summary in summaries:
            item_id = summary.get("itemId")
            if not item_id:
                continue
            if is_seen(db_path, item_id):
                continue
            try:
                item = api.get_item(item_id)
            except Exception as exc:  # noqa: BLE001
                logging.error("Failed to fetch item %s: %s", item_id, exc)
                continue
            raw_file.write(json.dumps(item) + "\n")

            score_result = score_item(item, min_feedback_pct, min_feedback_score)
            row = build_candidate_row(item, score_result, run_timestamp)
            candidates.append(row)
            mark_seen(db_path, item_id, run_timestamp)

    if not candidates:
        logging.info("No new candidates found.")
        return

    df = pd.DataFrame(candidates)
    df = df.sort_values(by=["score_total", "all_in_cost"], ascending=[False, True])

    output_path = data_dir / "candidates.csv"
    df.to_csv(output_path, index=False)
    logging.info("Wrote %d candidates to %s", len(df), output_path)


if __name__ == "__main__":
    main()
