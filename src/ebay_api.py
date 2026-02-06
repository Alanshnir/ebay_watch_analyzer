import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

EBAY_AUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_URL = "https://api.ebay.com/buy/browse/v1"


@dataclass
class TokenCache:
    access_token: Optional[str] = None
    expires_at: float = 0.0


class EbayApiError(RuntimeError):
    pass


class EbayApi:
    def __init__(self, client_id: str, client_secret: str, marketplace_id: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.marketplace_id = marketplace_id
        self._token_cache = TokenCache()

    def get_app_token(self) -> str:
        now = time.time()
        if self._token_cache.access_token and now < self._token_cache.expires_at - 60:
            return self._token_cache.access_token

        credentials = f"{self.client_id}:{self.client_secret}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }
        response = self._request_with_backoff("post", EBAY_AUTH_URL, headers=headers, data=data)
        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise EbayApiError(f"Missing access token in response: {payload}")
        expires_in = float(payload.get("expires_in", 3600))
        self._token_cache.access_token = access_token
        self._token_cache.expires_at = now + expires_in
        return access_token

    def search_items(
        self,
        q: str,
        category_ids: str,
        filters: str,
        limit: int = 50,
        offset: int = 0,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        token = self.get_app_token()
        params: Dict[str, Any] = {
            "q": q,
            "category_ids": category_ids,
            "filter": filters,
            "limit": limit,
            "offset": offset,
        }
        if sort:
            params["sort"] = sort
        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
        }
        url = f"{EBAY_BROWSE_URL}/item_summary/search"
        response = self._request_with_backoff("get", url, headers=headers, params=params)
        return response.json()

    def get_item(self, item_id: str) -> Dict[str, Any]:
        token = self.get_app_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
        }
        url = f"{EBAY_BROWSE_URL}/item/{item_id}"
        response = self._request_with_backoff("get", url, headers=headers)
        return response.json()

    def _request_with_backoff(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        retries = 5
        base_delay = 1.0
        for attempt in range(retries):
            response = requests.request(method, url, timeout=30, **kwargs)
            if response.status_code == 429:
                delay = base_delay * (2**attempt)
                logging.warning("Rate limited (429). Sleeping %.1fs", delay)
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                try:
                    details = response.json()
                except json.JSONDecodeError:
                    details = response.text
                if response.status_code == 401 and isinstance(details, dict):
                    error = details.get("error")
                    description = details.get("error_description")
                    if error == "invalid_client":
                        raise EbayApiError(
                            "HTTP 401 invalid_client: verify EBAY_CLIENT_ID/EBAY_CLIENT_SECRET "
                            "match your eBay app credentials and that you are using the production "
                            "credentials (not sandbox)."
                        )
                    if description:
                        raise EbayApiError(
                            f"HTTP 401 unauthorized: {description}. Check EBAY_CLIENT_ID/EBAY_CLIENT_SECRET."
                        )
                raise EbayApiError(f"HTTP {response.status_code} error: {details}")
            return response
        raise EbayApiError(f"Exceeded retries for {url}")
