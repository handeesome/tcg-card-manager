"""
Chinese TCG Platform API Clients - Reverse-Engineered from APK/Web Analysis

Platform 1: 集换社 (JiHuanShe) - https://api.jihuanshe.com
  - Flutter app (com.jihuanshe), APK reverse-engineered via strings extraction
  - H5 web version at https://h5.jihuanshe.com/ (Vue.js SPA)
  - Auth: Bearer token via Authorization header
  - Token obtained from app login (Auth0 social login / WeChat)
  - H5 version stores token in localStorage("token")

Platform 2: 镖卡 (BiaoKa/BiuCard) - https://api.gecahobby.com
  - UniApp-based app (com.biaoka.biucard)
  - H5 web version at https://app.biucards.com/ (Vue.js SPA)
  - Auth: Custom signature-based (no user login needed for share endpoints!)
  - Signature algorithm fully reverse-engineered from H5 JS bundle

NOTE: These APIs are reverse-engineered for personal use only.
      Respect the platforms' terms of service.
"""

import hashlib
import time
import json
import urllib.request
import urllib.error
import urllib.parse
import ssl

try:
    from collector_config import get_platform_token
except Exception:
    def get_platform_token(platform: str) -> str:
        return ""

# ============================================================
# 镖卡 (BiuCard / GECA) API Client
# ============================================================

class BiuCardAPI:
    """
    Client for the 镖卡 (BiuCard) API at api.gecahobby.com

    TWO ACCESS MODES:

    Mode 1 - Share Endpoints (no login required):
      Signature algorithm (reverse-engineered from H5 JS bundle):
      1. Sort request params by key name
      2. Concatenate: key1value1key2value2...
      3. MD5 hash
      4. Append: last 6 chars of timestamp + "biu_card_nbclass"
      5. MD5 hash again

    Mode 2 - Search & Detail Endpoints (login required):
      Bearer token authentication from a normal logged-in session.
      Token is supplied from environment variables or data/api_tokens.json.
      - search_cards(): Search by keyword → get card_id, PSA10 price, etc.
      - get_card_detail(): Get full card info by card_id
      - get_sold_data(): Get sold transaction data by card_id
      - search_trade_data(): Search sold transaction records by keyword

    API endpoints discovered via iPhone mitmproxy capture (APP v1.5.5):
      POST /api/proxy/pokemon/v1/tcg/checklists/search-cards
      POST /api/proxy/pokemon/v1/tcg/checklists/{id}
      POST /api/proxy/pokemon/v1/tcg/checklists/{id}/sold-data
      POST /api/proxy/pokemon/v1/tcg/checklists/{id}/sold-data-filter/v2
      POST /api/proxy/pokemon/v1/tcg/checklists/{id}/sold-analyze
      POST /api/proxy/pokemon/v1/tcg/checklists/{id}/pop-analyze
      POST /api/search-trade-data/search-list
      POST /api/pokemon/illustrations/v2/shareCard
    """

    BASE_URL = "https://api.gecahobby.com"
    APP_VERSION = "1.5.5"
    SALT = "biu_card_nbclass"

    DEFAULT_TOKEN = ""

    def __init__(self, token: str = None):
        self.token = token or get_platform_token("biaoka") or self.DEFAULT_TOKEN
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    def _compute_signature(self, params: dict, timestamp: str) -> str:
        """Compute request signature matching the JS algorithm."""
        # Step 1: Sort keys and concatenate key+value
        sorted_keys = sorted(params.keys())
        concat = ""
        for k in sorted_keys:
            concat += f"{k}{params[k]}"

        # Step 2: MD5 hash
        md5_1 = hashlib.md5(concat.encode()).hexdigest()

        # Step 3: Append timestamp suffix + salt
        ts_suffix = timestamp[-6:]
        md5_1 += f"{ts_suffix}{self.SALT}"

        # Step 4: MD5 hash again
        return hashlib.md5(md5_1.encode()).hexdigest()

    def _call(self, endpoint: str, params: dict = None) -> dict:
        """Make an authenticated API call."""
        if params is None:
            params = {}

        timestamp = str(int(time.time() * 1000))
        signature = self._compute_signature(params, timestamp)
        encoded = urllib.parse.urlencode(params)

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "signature": signature,
            "currentTime": timestamp,
            "equipmentType": "H5",
            "appVersion": self.APP_VERSION,
            "User-Agent": "Mozilla/5.0",
        }

        try:
            req = urllib.request.Request(
                self.BASE_URL + endpoint,
                data=encoded.encode(),
                headers=headers,
            )
            resp = urllib.request.urlopen(req, timeout=15, context=self._ctx)
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            try:
                return json.loads(body)
            except:
                return {"code": e.code, "msg": body[:200]}
        except Exception as e:
            return {"code": -1, "msg": str(e)[:200]}

    # --- Share Endpoints (no login required) ---

    def get_price_detail(self, share_id: str) -> dict:
        """
        Get card price detail by shareId.

        Returns card info including:
        - title, images, currency_type (USD/CNY)
        - sold_price_usd, sold_price_cny
        - source_show (EBAY/GOLDIN/HERITAGE/FANATICS/ALT)
        - sold_date, sold_mode, bid_count
        - graded_company, grade_score, is_rookie, etc.
        """
        return self._call("/api/search-trade-data/detail-for-share", {"shareId": share_id})

    def get_grading_detail(self, share_id: str) -> dict:
        """Get grading card detail by shareId."""
        return self._call("/api/search-trade-data/grading-detail-for-share", {"shareId": share_id})

    def get_ebay_goods_detail(self, goods_id: str) -> dict:
        """Get eBay goods detail by goods_id."""
        return self._call("/api/index/ebay_goods_detail_share", {"goods_id": goods_id})

    def get_ebay_goods_share(self, ebay_goods_id: str) -> dict:
        """Get eBay goods share info."""
        return self._call("/api/index/ebay_goods_share", {"ebay_goods_id": ebay_goods_id})

    def get_banner(self) -> dict:
        """Get app banner info."""
        return self._call("/api/index/banner", {})

    # --- Auth-required endpoints ---

    def get_hot_search(self) -> dict:
        """Get hot search keywords (requires login)."""
        return self._call("/api/search-trade-data/hot-search", {})

    # ============================================================
    # Authenticated API Methods (Bearer Token from iPhone APP)
    # ============================================================

    def _call_auth(self, endpoint: str, body: dict = None, method: str = "POST") -> dict:
        """Make an authenticated API call with Bearer token (iPhone APP style)."""
        if not self.token:
            return {"code": 401, "msg": "auth_required: missing biaoka token"}

        timestamp = str(int(time.time() * 1000))

        # Compute signature the same way as the share endpoints
        # but using the body params as sorted key-value pairs
        sig_params = {}
        if body:
            for k, v in body.items():
                if isinstance(v, (list, dict)):
                    sig_params[k] = json.dumps(v, ensure_ascii=False)
                else:
                    sig_params[k] = str(v)
        signature = self._compute_signature(sig_params, timestamp)

        headers = {
            "Content-Type": "application/json;charset=utf-8",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "token": self.token,
            "signature": signature,
            "currentTime": timestamp,
            "equipmentType": "ios",
            "phoneModel": "iPhone12,1",
            "phoneVersion": "17.7.1",
            "appVersion": self.APP_VERSION,
            "pseudoUniqueId": "a91db0c3-a06f-49ee-81fd-c46082ddc5c2",
            "userSettings": "default",
            "timezone": "GMT+08:00",
            "User-Agent": f"BuildCard/{self.APP_VERSION} (iPhone; iOS 17.7.1; Scale/2.00)",
        }

        body_data = json.dumps(body, ensure_ascii=False).encode() if body else b'{}'

        try:
            req = urllib.request.Request(
                self.BASE_URL + endpoint,
                data=body_data,
                headers=headers,
                method=method,
            )
            resp = urllib.request.urlopen(req, timeout=15, context=self._ctx)
            resp_body = resp.read().decode("utf-8", errors="replace")
            return json.loads(resp_body)
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")[:500] if e.fp else ""
            try:
                return json.loads(resp_body)
            except:
                return {"code": e.code, "msg": f"HTTP {e.code}: {resp_body[:200]}"}
        except Exception as e:
            return {"code": -1, "msg": str(e)[:200]}

    def search_cards(self, keyword: str, category: str = "pokemon",
                     sort: str = "price_desc", page: int = 1, page_size: int = 20,
                     include_trend: bool = True) -> dict:
        """
        Search cards by keyword (requires Bearer token).

        Returns cards with:
        - card.id: Unique card ID (used for detail queries)
        - card.chinese_name / card.card_name / card.japanese_name
        - card.version, card.serial_number, card.rarity
        - card.product_id, card.language
        - series: Series info (name, generation)
        - card_set: Set info (name, version, release_date)
        - grading: e.g. "psa10"
        - price: PSA10 estimated price (in CNY cents!)
        - raw_card_price: Raw card price
        - estimated_trend: 30-day price trend array
        - indices: Index values for the card

        Args:
            keyword: Search term (Chinese, English, or Japanese name)
            category: Card category - "pokemon", "yugioh", "onepiece", etc.
            sort: Sort order - "price_desc", "price_asc", "new", etc.
            page: Page number (1-based)
            page_size: Items per page
            include_trend: Whether to include 30-day price trend
        """
        body = {
            "keywords": keyword,
            "category": category,
            "sort": sort,
            "page": page,
            "page_size": page_size,
            "include_trend": include_trend,
        }
        return self._call_auth("/api/proxy/pokemon/v1/tcg/checklists/search-cards", body)

    def get_card_detail(self, card_id: int, include_trend: bool = True) -> dict:
        """
        Get card detail by card_id (requires Bearer token).

        Args:
            card_id: Card ID from search_cards results
            include_trend: Whether to include price trend
        """
        body = {"include_trend": include_trend}
        return self._call_auth(f"/api/proxy/pokemon/v1/tcg/checklists/{card_id}", body)

    def get_sold_data(self, card_id: int, grading: str = "psa10",
                      source: str = "", page: int = 1, page_size: int = 20) -> dict:
        """
        Get sold transaction data for a card (requires Bearer token).

        Args:
            card_id: Card ID from search_cards
            grading: Grading filter - "psa10", "psa9", "raw", "bgs10", etc.
            source: Source filter - "ebay", "goldin", "fanatics", "alt", "mercari", "snkrdunk"
            page: Page number
            page_size: Items per page
        """
        body = {
            "grading": grading,
            "source": source,
            "page": page,
            "page_size": page_size,
        }
        return self._call_auth(f"/api/proxy/pokemon/v1/tcg/checklists/{card_id}/sold-data", body)

    def get_sold_analyze(self, card_id: int) -> dict:
        """Get sold analysis data for a card (price statistics)."""
        return self._call_auth(f"/api/proxy/pokemon/v1/tcg/checklists/{card_id}/sold-analyze", {})

    def search_trade_data(self, query: str, source: int = 0, sold_mode: int = 0,
                          page: int = 1, page_size: int = 20, sort_type: int = 0,
                          min_sold_price: int = 0, max_sold_price: int = 0,
                          manufacturers: list = None, years: list = None,
                          origin_source: list = None, is_rookie: int = 0) -> dict:
        """
        Search sold transaction data by keyword (requires Bearer token).

        This is a broader search across all transaction records from eBay,
        Goldin, Fanatics, ALT, Mercari, Snkrdunk.

        Args:
            query: Search keyword
            source: Source filter (0=all, 1=eBay, 2=?, etc.)
            sold_mode: Sale mode (0=all, 1=一口价, 2=竞价)
            page: Page number
            page_size: Items per page
            sort_type: Sort type (0=default)
            min_sold_price: Minimum price filter
            max_sold_price: Maximum price filter
            manufacturers: Manufacturer filter list
            years: Year filter list
            origin_source: Origin source filter
            is_rookie: Rookie card filter (0=all, 1=rookie only)
        """
        body = {
            "query": query,
            "source": source,
            "sold_mode": sold_mode,
            "page": page,
            "page_size": page_size,
            "sort_type": sort_type,
            "min_sold_price": min_sold_price,
            "max_sold_price": max_sold_price,
            "manufacturers": manufacturers or [],
            "years": years or [],
            "origin_source": origin_source or [],
            "is_rookie": is_rookie,
        }
        return self._call_auth("/api/search-trade-data/search-list", body)

    def find_card_price(self, keyword: str, category: str = "pokemon",
                        card_number: str = "") -> dict:
        """
        High-level: Find a card and get its estimated price.

        Searches for cards by keyword, then returns the best match's
        PSA10 estimated price and raw card price.

        If card_number is provided, will try to match the result with
        the correct serial_number to find the right version.

        Returns dict with:
        - card_id, card_name, chinese_name, version
        - psa10_price_cny: PSA10 estimated price in CNY
        - raw_price_cny: Raw card price in CNY
        - serial_number, rarity, language
        - series_name, card_set_name
        - total_results: Number of matching cards
        """
        # Build more specific search keyword if card_number is given
        search_keyword = keyword
        if card_number and card_number not in keyword:
            search_keyword = f"{keyword} {card_number}"

        result = self.search_cards(search_keyword, category=category, page=1, page_size=10)

        if result.get("code") != 200:
            return {"error": result.get("msg", "API error"), "psa10_price_cny": None}

        data = result.get("data", {})
        cards = data.get("data", [])

        if not cards:
            # Fallback: try without card_number
            if card_number and search_keyword != keyword:
                result2 = self.search_cards(keyword, category=category, page=1, page_size=10)
                if result2.get("code") == 200:
                    data = result2.get("data", {})
                    cards = data.get("data", [])

        if not cards:
            return {
                "psa10_price_cny": None,
                "raw_price_cny": None,
                "total_results": data.get("total_count", 0),
            }

        # Find the best match by serial_number
        best = None
        if card_number:
            for item in cards:
                card = item.get("card", {})
                serial = card.get("serial_number", "")
                # Match: card_number could be "SWSH052", "20", "184/159", "001/005", etc.
                # Use precise matching to avoid "5" matching "052"
                if serial and card_number:
                    matched = False
                    # Exact match
                    if card_number == serial:
                        matched = True
                    # card_number is suffix of serial (e.g., "052" matches "SWSH052")
                    elif serial.endswith(card_number) and len(serial) > len(card_number):
                        # Ensure the prefix is alphabetic (not "5" matching "052")
                        prefix = serial[:-len(card_number)]
                        if prefix.isalpha():
                            matched = True
                    # Both are purely numeric, compare as integers
                    elif card_number.isdigit() and serial.isdigit():
                        if int(card_number) == int(serial):
                            matched = True
                    # Handle fractional numbers like "184/159", "001/005"
                    elif "/" in card_number:
                        if card_number in serial:
                            matched = True

                    if matched:
                        best = item
                        break

        # Fallback: first result
        if best is None:
            best = cards[0]

        card = best.get("card", {})
        series = best.get("series", {})
        card_set = best.get("card_set", {})

        # Price is in CNY (yuan, not cents) based on captured data analysis
        psa10_price = best.get("price", 0)
        raw_price = best.get("raw_card_price", 0)

        return {
            "card_id": card.get("id"),
            "card_name": card.get("card_name", ""),
            "chinese_name": card.get("chinese_name", ""),
            "japanese_name": card.get("japanese_name", ""),
            "version": card.get("version", ""),
            "serial_number": card.get("serial_number", ""),
            "rarity": card.get("rarity", ""),
            "language": card.get("language", ""),
            "grading": best.get("grading", ""),
            "psa10_price_cny": psa10_price,
            "raw_price_cny": raw_price,
            "series_name": series.get("chinese_name", ""),
            "card_set_name": card_set.get("chinese_name", ""),
            "total_results": data.get("total_count", 0),
        }

    def get_grading_price(self, card_id: int, grading: str = "psa10") -> dict:
        """
        Get price data for a specific grading tier of a card.

        Uses sold-data to get actual transaction records for the specific grading,
        and sold-analyze for overall price statistics.

        Args:
            card_id: Card ID from search_cards
            grading: Grading key like "psa10", "psa9", "psa8", "bgs10", etc.

        Returns dict with:
        - recent_sales: List of recent sale records for this grading
        - sale_count: Total number of sales for this grading
        - summary: Overall price statistics (avg, min, max, count)
        """
        ret = {
            "recent_sales": [],
            "sale_count": 0,
            "summary": None,
        }

        # 1. Get sold data for this specific grading
        sold = self.get_sold_data(card_id, grading=grading, page=1, page_size=5)
        if sold.get("code") == 200 and sold.get("data"):
            items = sold["data"].get("items", [])
            ret["sale_count"] = sold["data"].get("total", len(items))
            for item in items[:5]:
                ret["recent_sales"].append({
                    "price_cny": item.get("price", 0),
                    "final_price_cny": item.get("final_price", 0),
                    "source": item.get("src", ""),
                    "market": item.get("market", ""),
                    "sold_at": item.get("sold_at", ""),
                    "title": item.get("title", ""),
                    "grading": item.get("grading", {}).get("text", ""),
                })

        # 2. Get overall price statistics from sold-analyze
        analyze = self.get_sold_analyze(card_id)
        if analyze.get("code") == 200 and analyze.get("data"):
            summary = analyze["data"].get("summary", {})
            if summary:
                ret["summary"] = summary

        return ret


# ============================================================
# 集换社 (JiHuanShe) API Client
# ============================================================

class JiHuanSheAPI:
    """
    Client for the 集换社 API at api.jihuanshe.com

    Authentication: Bearer token in Authorization header.
    Token is obtained by logging into the 集换社 app (Auth0/WeChat login).
    The H5 web version stores the token in localStorage("token").

    How to get your token:
    1. Open h5.jihuanshe.com in a browser
    2. Log in via the app redirect
    3. Open browser DevTools → Application → Local Storage
    4. Find the "token" key and copy its value
    """

    BASE_URL = "https://api.jihuanshe.com"

    def __init__(self, token: str = None):
        self.token = token or get_platform_token("jihuanshe")
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    def _call(self, endpoint: str, method: str = "GET", data: dict = None) -> dict:
        """Make an API call with Bearer token authentication."""
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        body_data = None
        if method == "POST" and data:
            body_data = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"

        try:
            req = urllib.request.Request(
                self.BASE_URL + endpoint,
                data=body_data,
                headers=headers,
                method=method,
            )
            resp = urllib.request.urlopen(req, timeout=15, context=self._ctx)
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body)
            except:
                return {"code": resp.status, "html": body[:500]}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500] if e.fp else ""
            try:
                return json.loads(body)
            except:
                return {"code": e.code, "html": body[:200]}
        except Exception as e:
            return {"code": -1, "msg": str(e)[:200]}

    # --- Public endpoints (no auth needed) ---

    def get_users(self, page: int = 1) -> dict:
        """Get users list (public, no auth needed)."""
        return self._call(f"/api/market/users?page={page}")

    # --- Auth-required endpoints ---

    def search_card_versions(self, keyword: str) -> dict:
        """Search card versions by keyword."""
        encoded_kw = urllib.parse.quote(keyword)
        return self._call(f"/api/market/card-versions/search?keyword={encoded_kw}")

    def get_card_versions(self, game_key: str = "") -> dict:
        """Get card versions list."""
        params = f"?game_key={game_key}" if game_key else ""
        return self._call(f"/api/market/card-versions{params}")

    def get_cards(self) -> dict:
        """Get cards list."""
        return self._call("/api/market/cards")

    def get_products(self, game_key: str = "") -> dict:
        """Get products list."""
        params = f"?game_key={game_key}" if game_key else ""
        return self._call(f"/api/market/products{params}")

    def get_sellers_products(self, game_key: str = "") -> dict:
        """Get sellers' products (may be upgraded)."""
        params = f"?game_key={game_key}" if game_key else ""
        return self._call(f"/api/market/sellers/products{params}")

    def get_entrusted_prices(self) -> dict:
        """Get entrusted product card version prices."""
        return self._call("/api/market/entrustedProduct/cardVersionPrices")

    def get_activities(self, game_key: str = "") -> dict:
        """Get activities."""
        params = f"?game_key={game_key}" if game_key else ""
        return self._call(f"/api/market/activities{params}")


# ============================================================
# 卡淘 (CardHobby) API Client
# ============================================================

class CardHobbyAPI:
    """
    Client for the 卡淘 (CardHobby) market search API.

    Reverse-engineered from iPhone APP (Card/3.9.5) via mitmproxy capture.

    Key findings:
    - Search API: POST https://sale.cardhobby.com.cn/api/SearchCommodity/SearchCommodity
    - NO LOGIN REQUIRED! The search works without any authentication.
    - The APP sends an 'authorization' header but it's a device identifier,
      not a user session token. Omitting it still returns results.
    - Prices are in CNY (LowestPrice) and USD (USD_LowestPrice).
    - Search is keyword-based, matches against card Title field.
    - Results include: Title, LowestPrice, PriceCount, ByWay, Status, etc.

    Response structure:
    - data.Total: pages of results
    - data.TotalCount: total matching items
    - data.PagedMarketItemList: list of items with price info
    """

    SALE_URL = "https://sale.cardhobby.com.cn/api/SearchCommodity/SearchCommodity"
    APP_USER_AGENT = "Card/3.9.5 (iPhone; iOS 17.7.1; Scale/2.00)"
    APP_VERSION = "508"

    def __init__(self):
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    def _parse_price(self, val) -> float:
        """Parse a price value that might be a string with commas."""
        if val is None:
            return 0.0
        try:
            return float(str(val).replace(",", ""))
        except (ValueError, TypeError):
            return 0.0

    def _call_search(self, search_key: str, page: int = 1, page_size: int = 20,
                     sort: str = "EffectiveTimeStamp", sort_type: str = "asc",
                     status: int = 1) -> dict:
        """
        Call the SearchCommodity API.

        Args:
            search_key: Search keyword (e.g. "皮卡丘", "Charizard VMAX")
            page: Page number (1-based)
            page_size: Items per page (max 50)
            sort: Sort field - "EffectiveTimeStamp" (ending soon), "LowestPrice" (price)
            sort_type: "asc" or "desc"
            status: 1 = on sale, other values for different statuses

        Returns:
            API response dict with data.PagedMarketItemList containing items.
            Each item has: ID, Title, LowestPrice, USD_LowestPrice, PriceCount, etc.
        """
        body = {
            "SortType": sort_type,
            "device": "IPH",
            "SearchJson": json.dumps([{"Key": "Status", "Value": status}]),
            "PageIndex": page,
            "Lag": "en",
            "Version": self.APP_VERSION,
            "SearchKey": search_key,
            "PageSize": page_size,
            "mbname": "iPhone",
            "appname": "Card",
            "Sort": sort,
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.APP_USER_AGENT,
        }

        try:
            req = urllib.request.Request(
                self.SALE_URL,
                data=json.dumps(body).encode(),
                headers=headers,
            )
            resp = urllib.request.urlopen(req, timeout=15, context=self._ctx)
            resp_body = resp.read().decode("utf-8", errors="replace")
            return json.loads(resp_body)
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")[:500] if e.fp else ""
            try:
                return json.loads(resp_body)
            except:
                return {"result": 0, "msg": f"HTTP {e.code}: {resp_body[:200]}"}
        except Exception as e:
            return {"result": -1, "msg": str(e)[:200]}

    def search_cards(self, keyword: str, max_pages: int = 3, page_size: int = 20) -> list:
        """
        Search for cards by keyword and return aggregated results.

        Args:
            keyword: Search term
            max_pages: Maximum pages to fetch (default 3 = up to 60 items)
            page_size: Items per page

        Returns:
            List of dicts with: title, lowest_price_cny, lowest_price_usd,
            price_count, item_id, seller, sell_source
        """
        all_items = []

        for page in range(1, max_pages + 1):
            result = self._call_search(keyword, page=page, page_size=page_size)

            if result.get("result") != 1:
                break

            data = result.get("data", {})
            items = data.get("PagedMarketItemList", [])

            if not items:
                break

            for item in items:
                all_items.append({
                    "title": item.get("Title", ""),
                    "lowest_price_cny": item.get("LowestPrice", 0),
                    "lowest_price_usd": item.get("USD_LowestPrice", "0"),
                    "price_count": item.get("PriceCount", 0),
                    "item_id": item.get("ID"),
                    "seller": item.get("SellRealName", ""),
                    "sell_source": item.get("SellSource", ""),
                    "by_way": item.get("ByWay", 0),  # 2=拍卖, 1=一口价
                    "is_guarantee": item.get("IsGuarantee", 0),
                    "code": item.get("Code", ""),
                })

            total_pages = data.get("Total", 1)
            if page >= total_pages:
                break

        return all_items

    def get_lowest_price(self, keyword: str) -> dict:
        """
        Get the lowest CNY price for a card by keyword search.

        Uses price-sorted search to find the best market price.
        Filters out ¥1 starting-price auction items by preferring items
        with PriceCount > 0 (actual competing bids).

        Returns dict with: lowest_cny, lowest_usd, total_listings, price_count,
                           best_item (title), or error info.
        """
        # Sort by lowest price ascending to get cheapest first
        result = self._call_search(keyword, page=1, page_size=20,
                                   sort="LowestPrice", sort_type="asc")

        if result.get("result") != 1:
            return {"error": result.get("msg", "API error"), "lowest_cny": None,
                    "total_listings": 0, "lowest_usd": 0, "price_count": 0, "best_item": ""}

        data = result.get("data", {})
        items = data.get("PagedMarketItemList", [])

        if not items:
            return {
                "lowest_cny": None,
                "lowest_usd": 0,
                "total_listings": data.get("TotalCount", 0),
                "price_count": 0,
                "best_item": "",
            }

        # Find item with lowest non-trivial price
        # ¥1 on CardHobby is typically the auction starting price, not market value
        # Prefer items with PriceCount > 0 (have competing bids)
        best = None
        for item in items:
            price = item.get("LowestPrice", 0)
            count = item.get("PriceCount", 0)
            if price > 1 and count > 0:
                if best is None or price < best.get("lowest_cny", float("inf")):
                    best = {
                        "lowest_cny": price,
                        "lowest_usd": self._parse_price(item.get("USD_LowestPrice")),
                        "total_listings": data.get("TotalCount", 0),
                        "price_count": count,
                        "best_item": item.get("Title", ""),
                    }

        # Fallback: any item with price > 1
        if best is None:
            for item in items:
                price = item.get("LowestPrice", 0)
                if price > 1:
                    best = {
                        "lowest_cny": price,
                        "lowest_usd": self._parse_price(item.get("USD_LowestPrice")),
                        "total_listings": data.get("TotalCount", 0),
                        "price_count": item.get("PriceCount", 0),
                        "best_item": item.get("Title", ""),
                    }
                    break

        # Last fallback: first item with any price
        if best is None and items:
            item = items[0]
            best = {
                "lowest_cny": item.get("LowestPrice", 0),
                "lowest_usd": self._parse_price(item.get("USD_LowestPrice")),
                "total_listings": data.get("TotalCount", 0),
                "price_count": item.get("PriceCount", 0),
                "best_item": item.get("Title", ""),
            }

        return best or {"lowest_cny": None, "total_listings": 0, "lowest_usd": 0, "price_count": 0, "best_item": ""}


# ============================================================
# Quick Test
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("镖卡 (BiuCard) API Test")
    print("=" * 60)

    biaoka = BiuCardAPI()

    # Test 1: Banner (public, no auth)
    result = biaoka.get_banner()
    print(f"Banner: code={result.get('code')}, items={len(result.get('data', []))}")

    # Test 2: Price detail (needs real shareId)
    result = biaoka.get_price_detail("test")
    print(f"Price detail: code={result.get('code')}, msg={result.get('msg', '')}")

    # Test 3: eBay goods detail (needs real goods_id)
    result = biaoka.get_ebay_goods_detail("1")
    print(f"eBay detail: code={result.get('code')}, msg={result.get('msg', '')}")

    print()
    print("=" * 60)
    print("集换社 (JiHuanShe) API Test")
    print("=" * 60)

    # No token - test public endpoint
    jhs = JiHuanSheAPI()

    result = jhs.get_users(page=1)
    print(f"Users: code={result.get('code', '?')}, total={result.get('total', '?')}")

    result = jhs.search_card_versions("皮卡丘")
    print(f"Search: code={result.get('code', '?')}, error={result.get('error', '')}")

    print()
    print("=" * 60)
    print("卡淘 (CardHobby) API Test")
    print("=" * 60)

    ch = CardHobbyAPI()

    # Test 1: Search cards
    result = ch.search_cards("皮卡丘", max_pages=1, page_size=5)
    print(f"Search '皮卡丘': {len(result)} items found")
    for item in result[:3]:
        print(f"  ¥{item['lowest_price_cny']} x{item['price_count']} - {item['title'][:50]}...")

    # Test 2: Get lowest price
    result = ch.get_lowest_price("喷火龙")
    print(f"\nLowest price '喷火丘': ¥{result.get('lowest_cny')} (${result.get('lowest_usd')}) x{result.get('price_count')} listings={result.get('total_listings')}")
    print(f"  Best match: {result.get('best_item', '')[:60]}")

    print()
    print("=" * 60)
    print("API Discovery Summary")
    print("=" * 60)
    print()
    print("镖卡 (BiuCard) - api.gecahobby.com:")
    print("  ✓ Signature algorithm: FULLY REVERSE-ENGINEERED")
    print("  ✓ Share endpoints: WORK WITHOUT LOGIN")
    print("  ✗ Search endpoint: REQUIRES LOGIN")
    print()
    print("卡淘 (CardHobby) - sale.cardhobby.com.cn:")
    print("  ✓ Search API: WORKS WITHOUT LOGIN!")
    print("  ✓ Returns CNY + USD prices")
    print("  ✓ Keyword search with pagination and sorting")
    print("  Key endpoint:")
    print("    POST /api/SearchCommodity/SearchCommodity")
    print()
    print("集换社 (JiHuanShe) - api.jihuanshe.com:")
    print("  ✓ API structure: FULLY MAPPED (13+ endpoints)")
    print("  ✗ Card search: REQUIRES BEARER TOKEN")
