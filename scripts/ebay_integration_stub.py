"""
eBay integration stub for update_prices.py
This module provides a simple wrapper to call eBay Browse API if credentials are provided via env vars.
If no credentials are provided, functions return None and calling code should fallback to other sources.
"""
import os
import requests

def get_ebay_auth():
    client_id = os.environ.get('EBAY_CLIENT_ID')
    client_secret = os.environ.get('EBAY_CLIENT_SECRET')
    if not client_id or not client_secret:
        return None
    # For simplicity, assume the user will create a token externally and store in EBAY_OAUTH_TOKEN
    token = os.environ.get('EBAY_OAUTH_TOKEN')
    if token:
        return {'Authorization': f'Bearer {token}'}
    return None


def fetch_ebay_price(query):
    headers = get_ebay_auth()
    if not headers:
        return None
    # Example endpoint (Browse API) - user should ensure correct scope and token
    url = f'https://api.ebay.com/buy/browse/v1/item_summary/search?q={requests.utils.quote(query)}&limit=10'
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        # Extract simple median of price if present
        prices = []
        for it in data.get('itemSummaries', []):
            price = it.get('price', {}).get('value')
            if price:
                try:
                    prices.append(float(price))
                except:
                    continue
        if not prices:
            return None
        prices.sort()
        median = prices[len(prices)//2]
        return {'source':'ebay_api','median_usd':median,'raw':data}
    except Exception as e:
        return None
