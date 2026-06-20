"""
Reddit 社区价格参考源 [暂不可用 - 国内被墙]

从 Reddit r/PokemonCardValue, r/PKMNTCGtrades 等子版搜索价格讨论
使用公开 JSON API (URL加.json后缀)

价格是讨论参考价(非成交价), 以USD为主

⚠️ 国内无法直接访问Reddit，需要科学上网代理
如需启用，在下方设置代理: os.environ['https_proxy'] = 'http://127.0.0.1:PORT'
"""

import re
import json
import time
import os
import urllib.request
import urllib.parse
from datetime import datetime

# 国内访问Reddit需要代理，清除本地代理避免502
for proxy_key in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY']:
    os.environ.pop(proxy_key, None)

# 如果有可用的科学上网代理，在这里设置
# os.environ['https_proxy'] = 'http://127.0.0.1:7890'

REDDIT_USER_AGENT = "TCG-PriceTracker/1.0 (by /u/tcg_investor)"

# 重点关注的价格讨论子版
SUBREDDITS = [
    "PokemonCardValue",
    "PKMNTCGtrades",
    "PokemonTCG",
    "pokemontcgcollections",
]


def reddit_get(url, retries=3):
    """简单的Reddit JSON API请求"""
    req = urllib.request.Request(url, headers={
        "User-Agent": REDDIT_USER_AGENT,
        "Accept": "application/json",
    })

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                return {"error": str(e)}


def extract_usd_prices(text):
    """
    从文本中提取美元价格
    匹配: $100, $1,000, 100 usd, 100 dollars 等
    """
    prices = []

    # $100, $1,000.50
    for m in re.finditer(r'\$\s*([\d,]+(?:\.\d+)?)', text, re.IGNORECASE):
        val = m.group(1).replace(',', '')
        try:
            prices.append(float(val))
        except ValueError:
            pass

    # 100 usd / 100 dollars
    for m in re.finditer(r'([\d,]+(?:\.\d+)?)\s*(?:usd|dollars?)', text, re.IGNORECASE):
        val = m.group(1).replace(',', '')
        try:
            prices.append(float(val))
        except ValueError:
            pass

    return prices


def search_reddit(keyword, subreddits=None, limit=10, time_filter="month"):
    """
    搜索Reddit帖子，提取价格讨论

    Args:
        keyword: 搜索关键词 (如 "PSA10 Pikachu")
        subreddits: 要搜索的子版列表 (默认用全部)
        limit: 每个子版返回的帖子数
        time_filter: 时间范围 (hour/day/week/month/year/all)

    Returns:
        dict with:
        - posts: 帖子列表 [{title, url, score, created, prices, subreddit}]
        - price_summary: {min, max, avg, median, count}
        - query: 搜索关键词
    """
    if subreddits is None:
        subreddits = SUBREDDITS

    all_posts = []

    for sub in subreddits:
        query = urllib.parse.quote(keyword)
        url = (
            f"https://www.reddit.com/r/{sub}/search.json"
            f"?q={query}&sort=new&restrict_sr=on"
            f"&t={time_filter}&limit={limit}"
        )

        data = reddit_get(url)
        if data.get("error"):
            continue

        listings = data.get("data", {}).get("children", [])

        for listing in listings:
            post = listing.get("data", {})
            title = post.get("title", "")
            selftext = post.get("selftext", "")
            combined = f"{title} {selftext}"

            # 提取价格
            prices = extract_usd_prices(combined)

            if prices:
                all_posts.append({
                    "title": title,
                    "url": f"https://reddit.com{post.get('permalink', '')}",
                    "score": post.get("score", 0),
                    "created": datetime.fromtimestamp(post.get("created_utc", 0)).isoformat(),
                    "prices": prices,
                    "subreddit": sub,
                    "snippet": selftext[:200] if selftext else "",
                })

        # 限流: Reddit要求每分钟不超过60次
        time.sleep(1.5)

    # 汇总价格统计
    all_prices = []
    for post in all_posts:
        all_prices.extend(post["prices"])

    summary = {}
    if all_prices:
        all_prices.sort()
        summary = {
            "min": min(all_prices),
            "max": max(all_prices),
            "avg": round(sum(all_prices) / len(all_prices), 2),
            "median": all_prices[len(all_prices) // 2],
            "count": len(all_prices),
        }

    return {
        "query": keyword,
        "posts": all_posts,
        "price_summary": summary,
        "total_posts": len(all_posts),
    }


def search_card_price(card_name, card_number="", grade=""):
    """
    高级搜索: 根据卡牌信息构造搜索词

    Args:
        card_name: 卡牌名 (英文名更适合Reddit)
        card_number: 卡号 (如 "SWSH052")
        grade: 评级 (如 "PSA10", "PSA9")

    Returns:
        dict with price_summary 和 posts
    """
    # Reddit用英文名搜索更有效
    search_terms = []

    if grade:
        search_terms.append(f"{grade} {card_name}")
    if card_number:
        search_terms.append(f"{card_name} {card_number}")
    search_terms.append(card_name)

    best_result = None

    for term in search_terms:
        result = search_reddit(term, limit=5, time_filter="year")

        if result["total_posts"] > 0:
            if best_result is None or result["total_posts"] > best_result["total_posts"]:
                best_result = result
                best_result["search_term"] = term

            # 找到足够多的结果就停
            if result["total_posts"] >= 3:
                break

    if best_result is None:
        return {
            "query": card_name,
            "posts": [],
            "price_summary": {},
            "total_posts": 0,
            "search_term": card_name,
        }

    return best_result


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python reddit_price.py search <关键词>")
        print("      python reddit_price.py card <卡名> [卡号] [评级]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "search":
        keyword = " ".join(sys.argv[2:])
        print(f"搜索: {keyword}")
        result = search_reddit(keyword, limit=10, time_filter="year")

        if result["price_summary"]:
            s = result["price_summary"]
            print(f"\n价格统计: ${s['min']:.0f} ~ ${s['max']:.0f}, "
                  f"均价 ${s['avg']:.0f}, 中位数 ${s['median']:.0f} ({s['count']}个价格)")
        else:
            print("未找到价格讨论")

        for post in result["posts"][:5]:
            print(f"\n  [{post['subreddit']}] {post['title'][:60]}")
            print(f"  价格: {post['prices']} | 分数: {post['score']}")

    elif cmd == "card":
        name = sys.argv[2] if len(sys.argv) > 2 else ""
        number = sys.argv[3] if len(sys.argv) > 3 else ""
        grade = sys.argv[4] if len(sys.argv) > 4 else ""

        result = search_card_price(name, card_number=number, grade=grade)
        print(json.dumps(result, indent=2, ensure_ascii=False))
