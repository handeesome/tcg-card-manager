#!/usr/bin/env python3
"""
TCG 持仓看板 - 价格更新脚本
数据源优先级 (USD): tcgapi.dev > TCGPriceLookup > PokePrice > SerpAPI/eBay > 镖卡 > PriceCharting
数据源优先级 (CNY): 卡淘 > 镖卡CNY > ACE10估值 > USD*汇率
汇率: 1 USD ≈ 7.2 CNY
"""
import json, os, sys, time, re, shutil
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("需要 requests 库，尝试安装...")
    os.system(f"{sys.executable} -m pip install requests")
    import requests

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / 'data'
PORTFOLIO = DATA_DIR / 'portfolio.json'
BACKUP_DIR = DATA_DIR / 'backups'
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).parent))
from collector_config import get_api_key

TCGAPI_KEY = get_api_key("tcgapi")
SERPAPI_KEY = get_api_key("serpapi")
TCGPRICELOOKUP_KEY = get_api_key("tcgpricelookup")
POKEPRICE_KEY = get_api_key("pokeprice")
USD2CNY = 7.2

# 镖卡 (BiuCard) - 逆向签名API (无需登录)
from chinese_platform_api import BiuCardAPI, CardHobbyAPI

# PriceCharting 网址模板 (仅Pokemon)
PRICECHARTING_BASE = "https://www.pricecharting.com/game/pokemon-"


def backup_portfolio():
    """创建备份"""
    ts = datetime.now().strftime('%Y%m%d%H%M%S')
    bak = BACKUP_DIR / f"portfolio.json.{ts}.bak"
    if PORTFOLIO.exists():
        shutil.copy2(PORTFOLIO, bak)
        print(f"  备份: {bak}")
    return bak


def load_portfolio():
    with open(PORTFOLIO, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_portfolio(data):
    tmp = PORTFOLIO.with_suffix('.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PORTFOLIO)
    print(f"  已保存: {PORTFOLIO}")


def fetch_tcgapi(card):
    """从 tcgapi.dev 获取价格"""
    name = card.get('name_en') or card.get('name_cn') or ''
    card_number = card.get('card_number', '')
    game = card.get('game', '')
    
    if game != 'pokemon':
        print(f"  [跳过] tcgapi 仅支持 Pokemon: {name}")
        return None

    if not TCGAPI_KEY:
        print("  [tcgapi] 缺少 TCGAPI_KEY，跳过")
        return None
    
    url = "https://api.tcgapi.dev/v1/search"
    params = {"q": name, "game": "pokemon", "limit": 10}
    headers = {"X-API-Key": TCGAPI_KEY}
    
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        products = data.get('data', [])
        if not products:
            print(f"  [tcgapi] 未找到: {name}")
            return None
        
        # 尝试匹配最准确的产品
        best = None
        for prod in products:
            prod_name = (prod.get('name') or '').lower()
            prod_num = (prod.get('number') or '')
            # 优先匹配卡号
            if card_number and prod_num and card_number in prod_num:
                best = prod
                break
            # 其次匹配名称
            if name.lower() in prod_name:
                best = prod
                break
        
        if not best:
            best = products[0]
        
        # 提取价格
        market = best.get('market_price') or best.get('marketPrice')
        low = best.get('low_price') or best.get('lowPrice')
        median = best.get('median_price') or best.get('midPrice')
        
        result = {
            'price_usd': float(market) if market else None,
            'low_usd': float(low) if low else None,
            'median_usd': float(median) if median else None,
            'source': 'tcgapi.dev',
            'source_name': best.get('name', ''),
            'set_name': best.get('set_name', best.get('setName', '')),
            'image_url': best.get('image_url', best.get('imageUrl', '')),
        }
        
        # 如果market为空但有low，用low
        if result['price_usd'] is None and result['low_usd'] is not None:
            result['price_usd'] = result['low_usd']
        
        print(f"  [tcgapi] {name}: market=${result['price_usd']} low=${result['low_usd']} median=${result['median_usd']}")
        return result
        
    except Exception as e:
        print(f"  [tcgapi] 错误: {e}")
        return None


def fetch_pricecharting(card):
    """从 PriceCharting 获取PSA评级价格 (仅Pokemon)"""
    name = card.get('name_en') or card.get('name_cn') or ''
    game = card.get('game', '')
    series = card.get('series', '')
    card_number = card.get('card_number', '')
    
    if game != 'pokemon':
        print(f"  [跳过] PriceCharting 仅支持 Pokemon: {name}")
        return None
    
    # PriceCharting URL 构造 - 根据系列映射
    # 常见系列映射 (不完全，后续可扩展)
    series_map = {
        'pokemon futsal collection': 'futsal-promos',
        'swsh celebrations': 'celebrations',
        "swsh champion's path": 'champions-path',
        'black star promo': 'promos',
        'swsh black star promo': 'promos',
        'sv09 journey together': 'journey-together',
    }
    
    slug = None
    for key, val in series_map.items():
        if key in (series or '').lower():
            slug = val
            break
    
    if not slug:
        # 尝试通用搜索
        print(f"  [PriceCharting] 无系列映射: {series}，跳过")
        return None
    
    url = f"{PRICECHARTING_BASE}{slug}"
    
    try:
        r = requests.get(url, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        r.raise_for_status()
        html = r.text
        
        # 在页面中搜索卡号对应的行
        result = {
            'psa10_usd': None,
            'psa9_usd': None,
            'psa8_usd': None,
            'raw_usd': None,
            'source': 'pricecharting.com',
        }
        
        # 尝试从HTML中提取PSA价格
        # PriceCharting 页面通常有 graded-prices 表格
        # 使用正则匹配 (格式可能变化，这里做最佳尝试)
        
        # 搜索包含卡牌名称的区域
        name_lower = name.lower().replace('☆', '').strip()
        
        # 尝试匹配 PSA 10 价格
        psa10_match = re.search(r'PSA\s*10[^$]*\$\s*([\d,]+\.?\d*)', html, re.IGNORECASE)
        psa9_match = re.search(r'PSA\s*9[^0-9][^$]*\$\s*([\d,]+\.?\d*)', html, re.IGNORECASE)
        psa8_match = re.search(r'PSA\s*8[^0-9][^$]*\$\s*([\d,]+\.?\d*)', html, re.IGNORECASE)
        raw_match = re.search(r'Ungraded[^$]*\$\s*([\d,]+\.?\d*)', html, re.IGNORECASE)
        
        if psa10_match:
            result['psa10_usd'] = float(psa10_match.group(1).replace(',', ''))
        if psa9_match:
            result['psa9_usd'] = float(psa9_match.group(1).replace(',', ''))
        if psa8_match:
            result['psa8_usd'] = float(psa8_match.group(1).replace(',', ''))
        if raw_match:
            result['raw_usd'] = float(raw_match.group(1).replace(',', ''))
        
        if any([result['psa10_usd'], result['psa9_usd'], result['psa8_usd'], result['raw_usd']]):
            print(f"  [PriceCharting] {name}: PSA10=${result['psa10_usd']} PSA9=${result['psa9_usd']} PSA8=${result['psa8_usd']} Raw=${result['raw_usd']}")
            return result
        else:
            print(f"  [PriceCharting] 未找到PSA价格: {name}")
            return None
            
    except Exception as e:
        print(f"  [PriceCharting] 错误: {e}")
        return None


def fetch_tcgpricelookup(card):
    """
    通过 TCGPriceLookup API 获取 TCGPlayer 裸卡市场价
    免费版: 200次/天, 仅含 raw (ungraded) TCGPlayer 价格
    支持: pokemon, pokemon-jp, yugioh, onepiece, mtg, lorcana, fab, swu
    """
    name = card.get('name_en') or card.get('name_cn') or ''
    card_number = card.get('card_number', '')
    game = card.get('game', '')
    
    # 游戏映射
    game_map = {
        'pokemon': 'pokemon',
        'yugioh': 'yugioh',
        'onepiece': 'onepiece',
        'conan': None,  # TCGPriceLookup 不支持柯南TCG
    }
    api_game = game_map.get(game)
    if not api_game:
        print(f"  [TCGPriceLookup] 不支持的游戏: {game}")
        return None

    if not TCGPRICELOOKUP_KEY:
        print("  [TCGPriceLookup] 缺少 TCGPRICELOOKUP_KEY，跳过")
        return None
    
    # 构造搜索词
    query = name
    if card_number:
        query = f"{name} {card_number}"
    
    url = "https://api.tcgpricelookup.com/v1/cards/search"
    params = {'q': query, 'game': api_game}
    headers = {'X-API-Key': TCGPRICELOOKUP_KEY}
    
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        if r.status_code == 429:
            print(f"  [TCGPriceLookup] 请求频率限制，跳过")
            return None
        r.raise_for_status()
        data = r.json()
        
        items = data.get('data', [])
        if not items:
            print(f"  [TCGPriceLookup] 未找到: {query}")
            return None
        
        # 找最佳匹配（优先匹配卡号）
        best = None
        for item in items:
            item_num = item.get('number', '')
            item_name = (item.get('name') or '').lower()
            if card_number and item_num == card_number:
                best = item
                break
            if name.lower() in item_name:
                best = item
                break
        if not best:
            best = items[0]
        
        # 提取价格 - 免费版只有 raw TCGPlayer 数据
        prices_data = best.get('prices', {})
        result = {
            'source': 'tcgpricelookup.com',
            'card_name': best.get('name', ''),
            'card_number': best.get('number', ''),
            'variant': best.get('variant', ''),
            'set_name': best.get('set', {}).get('name', ''),
            'game': api_game,
        }
        
        # 提取 raw near_mint TCGPlayer market price
        raw_nm = prices_data.get('raw', {}).get('near_mint', {}).get('tcgplayer', {})
        market = raw_nm.get('market')
        low = raw_nm.get('low')
        mid = raw_nm.get('mid')
        high = raw_nm.get('high')
        
        result['raw_near_mint_market_usd'] = float(market) if market and market > 0 else None
        result['raw_near_mint_low_usd'] = float(low) if low and low > 0 else None
        result['raw_near_mint_mid_usd'] = float(mid) if mid else None
        result['raw_near_mint_high_usd'] = float(high) if high and high > 0 else None
        
        # 提取各品相价格（如果有）
        conditions = ['near_mint', 'lightly_played', 'moderately_played', 'damaged']
        for cond in conditions:
            cond_data = prices_data.get('raw', {}).get(cond, {}).get('tcgplayer', {})
            cond_market = cond_data.get('market')
            if cond_market and cond_market > 0:
                result[f'raw_{cond}_market_usd'] = float(cond_market)
        
        # 检查是否有 graded 数据（Trader 版才有）
        graded_keys = [k for k in prices_data.keys() if k not in ('raw',)]
        if graded_keys:
            result['graded_available'] = True
            result['graded_types'] = graded_keys
        else:
            result['graded_available'] = False
        
        price_val = result.get('raw_near_mint_market_usd')
        if price_val:
            print(f"  [TCGPriceLookup] {name}: Raw NM=${price_val} (TCGPlayer market)")
        else:
            print(f"  [TCGPriceLookup] {name}: 无裸卡NM市场价")
        
        return result
        
    except Exception as e:
        print(f"  [TCGPriceLookup] 错误: {e}")
        return None


def fetch_cardhobby(card):
    """
    从卡淘 (CardHobby) 市场搜索API获取CNY价格
    逆向自iPhone APP (Card/3.9.5)，无需登录即可搜索！

    返回数据包含:
    - LowestPrice: 最低CNY价格（人民币）
    - USD_LowestPrice: 最低USD价格
    - PriceCount: 有多少个在售
    - Title: 商品标题（含卡牌名称、系列、编号等）
    - SellSource: 来源地 (CN=中国, JP=日本等)

    注意: 卡淘是CNY价格最可靠的来源，因为它是最大的中国球星卡交易平台
    搜索策略: 中文名+卡号 → 英文名+卡号 → 中文名 → 英文名
    """
    name_en = card.get('name_en') or ''
    name_cn = card.get('name_cn') or ''
    card_number = card.get('card_number', '')
    series = card.get('series', '')

    ch = CardHobbyAPI()

    # 构造搜索关键词列表，按优先级
    search_keys = []

    # 1. 中文名 + 卡号 (最精确)
    if name_cn and card_number:
        search_keys.append(f"{name_cn} {card_number}")

    # 2. 英文名 + 卡号
    if name_en and card_number:
        search_keys.append(f"{name_en} {card_number}")

    # 3. 中文名
    if name_cn:
        search_keys.append(name_cn)

    # 4. 英文名
    if name_en and name_en != name_cn:
        search_keys.append(name_en)

    # 5. 英文名缩写/别名 (特典卡等)
    # 卡淘对Futsal等特典卡名搜索不友好，补充中文别名
    alt_names = {
        'Pikachu on the Ball': '足球皮卡丘',
        'Special Delivery Charizard': '特殊配送喷火龙',
        'Greninja ☆': '甲贺忍蛙星星',
    }
    if name_en in alt_names:
        search_keys.append(alt_names[name_en])

    best_result = None

    for search_key in search_keys:
        result = ch.get_lowest_price(search_key)

        if result.get('error') or result.get('lowest_cny') is None:
            continue

        lowest_cny = result.get('lowest_cny', 0)

        # 跳过¥1的拍卖起拍价（不是真实市场价）
        if lowest_cny <= 1:
            continue

        if best_result is None or lowest_cny < best_result.get('lowest_cny', float('inf')):
            best_result = result
            best_result['search_key_used'] = search_key
            # 如果结果数<=50，认为匹配足够精确
            if result.get('total_listings', 0) <= 50:
                break

    if best_result is None:
        # 最后尝试：即使只有¥1的结果也返回
        for search_key in search_keys[:3]:  # 只试前三个
            result = ch.get_lowest_price(search_key)
            if result.get('lowest_cny') is not None and result.get('lowest_cny', 0) > 0:
                best_result = result
                best_result['search_key_used'] = search_key
                break

    if best_result is None:
        print(f"  [卡淘] 未找到: {name_cn or name_en}")
        return None

    ret = {
        'source': 'cardhobby.com.cn (卡淘)',
        'search_key': best_result.get('search_key_used', search_keys[0] if search_keys else ''),
        'lowest_cny': best_result.get('lowest_cny', 0),
        'lowest_usd': best_result.get('lowest_usd', 0),
        'price_count': best_result.get('price_count', 0),
        'total_listings': best_result.get('total_listings', 0),
        'best_match': best_result.get('best_item', '')[:80],
    }

    # 警告：搜索结果太多或价格异常
    if ret['total_listings'] > 100:
        ret['accuracy_warning'] = '搜索结果过多，价格可能不精确'
    if ret['lowest_cny'] <= 1:
        ret['accuracy_warning'] = '价格¥1可能是拍卖起拍价，非市场价'

    print(f"  [卡淘] {ret['search_key']}: ¥{ret['lowest_cny']} (${ret['lowest_usd']}) x{ret['price_count']} 总{ret['total_listings']}条")
    if ret.get('accuracy_warning'):
        print(f"         ⚠️ {ret['accuracy_warning']}")

    return ret


def fetch_biaoka(card):
    """
    从镖卡 (BiuCard) API 获取卡牌价格数据
    使用 Bearer Token 认证（iPhone APP抓包获取），支持关键词搜索！

    镖卡数据包含:
    1. 卡牌数据库搜索 (search-cards): PSA10估值 + 裸卡价格
    2. 成交分析 (sold-analyze): 各评级估值 + 价格趋势
    3. 成交记录 (sold-data): eBay/Goldin/Fanatics/ALT 实际成交价

    数据源: eBay, Goldin, Fanatics, ALT, Mercari, Snkrdunk
    价格: CNY (price字段) + 汇率转换USD
    """
    name_en = card.get('name_en') or ''
    name_cn = card.get('name_cn') or ''
    card_number = card.get('card_number', '')
    game = card.get('game', '')
    series = card.get('series', '')
    grading = card.get('grading', {})
    grader = grading.get('grader', '').upper()
    grade = grading.get('grade', None)

    bk = BiuCardAPI()

    # 确定搜索分类
    category_map = {
        'pokemon': 'pokemon',
        'yugioh': 'yugioh',
        'onepiece': 'onepiece',
        'one piece': 'onepiece',
    }
    category = category_map.get((game or '').lower(), 'pokemon')

    # 搜索关键词列表 - 优先用英文名+卡号精确匹配
    search_keys = []

    # 从 series 名中提取可能的 set prefix (如 SWSH, SV09 等)
    series_prefix = ''
    series_lower = (series or '').lower()
    if 'swsh' in series_lower or 'sword & shield' in series_lower or '剑盾' in series:
        series_prefix = 'SWSH'
    elif 'sv09' in series_lower or 'journey together' in series_lower:
        series_prefix = ''  # No standard prefix for this set
    elif 'celebrations' in series_lower or '25th' in series_lower:
        series_prefix = 'SWSH'

    # 构造搜索词：用 set prefix + card_number 更精确
    full_card_number = card_number
    if series_prefix and card_number and not card_number.startswith(series_prefix):
        full_card_number = f"{series_prefix}{card_number}"

    if name_en and full_card_number and full_card_number != card_number:
        search_keys.append(f"{name_en} {full_card_number}")
    if name_en and card_number:
        search_keys.append(f"{name_en} {card_number}")
    if name_en:
        search_keys.append(name_en)
    if name_cn and name_cn != name_en:
        search_keys.append(name_cn)

    best_result = None

    # 1. 用 search_cards + card_number 精确搜索
    for search_key in search_keys:
        result = bk.find_card_price(search_key, category=category, card_number=card_number)

        if result.get('error') or result.get('psa10_price_cny') is None:
            continue

        if best_result is None:
            best_result = result
            best_result['search_key_used'] = search_key
            # 如果结果数<=20，匹配够精确
            if result.get('total_results', 0) <= 20:
                break

    if best_result is None:
        print(f"  [镖卡] 未找到: {name_cn or name_en}")
        return None

    ret = {
        'source': 'biucards.com (镖卡)',
        'search_key': best_result.get('search_key_used', ''),
        'card_id': best_result.get('card_id'),
        'card_name': best_result.get('card_name', ''),
        'chinese_name': best_result.get('chinese_name', ''),
        'serial_number': best_result.get('serial_number', ''),
        'psa10_price_cny': best_result.get('psa10_price_cny'),
        'raw_price_cny': best_result.get('raw_price_cny'),
        'series_name': best_result.get('series_name', ''),
        'card_set_name': best_result.get('card_set_name', ''),
        'total_results': best_result.get('total_results', 0),
    }

    # 2. 如果有card_id，获取对应评级的精确价格
    card_id = best_result.get('card_id')
    if card_id:
        # 确定要查询的评级key
        grading_key = None
        if grader == 'PSA' and grade:
            grading_key = f"psa{grade}"
        elif grader == 'ACE' and grade == 10:
            # ACE10 ≈ PSA9-10之间，镖卡没有ACE数据，查PSA10参考
            grading_key = "psa10"
        elif grader == 'BGS' and grade:
            grading_key = f"bgs{grade}"
        elif grader == 'CGC' and grade:
            grading_key = f"cgc{grade}"

        # 2a. 用 get_grading_price 获取评级成交数据
        if grading_key:
            try:
                gp = bk.get_grading_price(card_id, grading=grading_key)
                ret['grade_key'] = grading_key
                ret['grade_sale_count'] = gp.get('sale_count', 0)
                if gp.get('recent_sales'):
                    latest = gp['recent_sales'][0]
                    ret['latest_sold_cny'] = latest.get('price_cny')
                    ret['latest_sold_source'] = latest.get('market', '')
                    ret['latest_sold_date'] = latest.get('sold_at', '')
                    ret['latest_sold_grade'] = latest.get('grading', '')
                if gp.get('summary'):
                    ret['biaoka_summary'] = gp['summary']

                # 2b. 如果当前评级没有成交数据，尝试PSA10作为参考
                if not gp.get('recent_sales') and grading_key != 'psa10':
                    gp10 = bk.get_grading_price(card_id, grading='psa10')
                    if gp10.get('recent_sales'):
                        ret['psa10_ref_sold_cny'] = gp10['recent_sales'][0].get('price_cny')
                        ret['psa10_ref_source'] = gp10['recent_sales'][0].get('market', '')
            except Exception as e:
                print(f"  [镖卡] 获取评级价格失败: {e}")

    # 打印结果
    parts = []
    if ret.get('psa10_price_cny') and ret['psa10_price_cny'] > 0:
        parts.append(f"PSA10=¥{ret['psa10_price_cny']:.0f}")
    if ret.get('grade_sale_count', 0) > 0:
        parts.append(f"{ret.get('grade_key','').upper()} {ret['grade_sale_count']}条成交")
    if ret.get('latest_sold_cny'):
        parts.append(f"最近成交=¥{ret['latest_sold_cny']:.0f}")
    if ret.get('latest_sold_source'):
        parts.append(f"来源:{ret['latest_sold_source']}")
    if not gp.get('recent_sales') if 'gp' in dir() else True:
        if ret.get('psa10_ref_sold_cny'):
            parts.append(f"PSA10参考=¥{ret['psa10_ref_sold_cny']:.0f}")

    search_key = ret.get('search_key', name_cn or name_en)
    print(f"  [镖卡] {search_key}: {' / '.join(parts) if parts else '无价格数据'}")
    if ret.get('serial_number'):
        print(f"         #{ret['serial_number']} {ret.get('series_name','')}")

    return ret


def fetch_pokeprice(card):
    """
    从 PokemonPriceTracker API 获取价格数据
    免费版: 100 credits/天, 2 req/min
    仅 Pokemon, 提供 TCGPlayer 裸卡市场价 + 变体价格
    PSA评级价/eBay数据需要付费版
    """
    name = card.get('name_en') or card.get('name_cn') or ''
    card_number = card.get('card_number', '')
    game = card.get('game', '')

    if game != 'pokemon':
        print(f"  [PokePrice] 跳过非Pokemon: {name}")
        return None

    if not POKEPRICE_KEY:
        print("  [PokePrice] 缺少 POKEPRICE_KEY，跳过")
        return None

    url = "https://www.pokemonpricetracker.com/api/v2/cards"
    headers = {"Authorization": f"Bearer {POKEPRICE_KEY}"}
    params = {"search": name, "limit": 10}

    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        if r.status_code == 429:
            print(f"  [PokePrice] 速率限制，跳过")
            return None
        if r.status_code == 401:
            print(f"  [PokePrice] API Key 无效或过期")
            return None
        r.raise_for_status()
        data = r.json()

        items = data.get('data', [])
        if not items:
            print(f"  [PokePrice] 未找到: {name}")
            return None

        # 找最佳匹配（优先匹配卡号）
        best = None
        for item in items:
            item_num = item.get('cardNumber', '')
            item_name = (item.get('name') or '').lower()
            if card_number and item_num == card_number:
                best = item
                break
            if name.lower() in item_name:
                best = item
                break
        if not best:
            best = items[0]

        prices_data = best.get('prices', {})
        result = {
            'source': 'pokemonpricetracker.com',
            'card_name': best.get('name', ''),
            'card_number': best.get('cardNumber', ''),
            'set_name': best.get('setName', ''),
            'tcgplayer_id': best.get('tcgPlayerId', ''),
            'tcgplayer_url': best.get('tcgPlayerUrl', ''),
        }

        # 提取市场价 (TCGPlayer 裸卡 Near Mint)
        market = prices_data.get('market')
        low = prices_data.get('low')
        if market:
            result['raw_market_usd'] = float(market)
        if low:
            result['raw_low_usd'] = float(low)

        # 提取变体价格
        variants = prices_data.get('variants', {})
        for variant_name, variant_data in variants.items():
            if isinstance(variant_data, dict):
                for condition, cond_data in variant_data.items():
                    if isinstance(cond_data, dict) and cond_data.get('price'):
                        key = f'{variant_name}_{condition.replace(" ", "_")}'.lower()
                        result[key + '_usd'] = float(cond_data['price'])

        # 打印结果
        parts = []
        if result.get('raw_market_usd'):
            parts.append(f"Market=${result['raw_market_usd']}")
        if result.get('raw_low_usd'):
            parts.append(f"Low=${result['raw_low_usd']}")

        print(f"  [PokePrice] {name}: {' / '.join(parts) if parts else '无价格数据'}")

        return result

    except Exception as e:
        print(f"  [PokePrice] 错误: {e}")
        return None


def fetch_serpapi_ebay(card):
    """
    通过 SerpAPI 搜索 eBay 已售商品，获取真实成交价
    免费额度: 100次/月 (相同查询1小时内缓存免费)
    支持: 所有游戏、所有语言、PSA/ACE 评级卡
    """
    name = card.get('name_en') or card.get('name_cn') or ''
    card_number = card.get('card_number', '')
    grading = card.get('grading', {})
    grade = grading.get('grade', None)
    grader = grading.get('grader', '').upper()

    if not SERPAPI_KEY:
        print("  [SerpAPI] 缺少 SERPAPI_KEY，跳过")
        return None
    
    # 构造搜索关键词
    query_parts = [name]
    if card_number:
        query_parts.append(card_number)
    if grader and grade:
        query_parts.append(f"{grader} {grade}")
    search_query = ' '.join(query_parts)
    
    url = "https://serpapi.com/search"
    params = {
        'engine': 'ebay',
        '_nkw': search_query,
        'ebay_domain': 'ebay.com',
        'show_only': 'Sold',
        'api_key': SERPAPI_KEY,
    }
    
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        
        if 'error' in data:
            print(f"  [SerpAPI] 错误: {data['error']}")
            return None
        
        results = data.get('organic_results', [])
        if not results:
            print(f"  [SerpAPI] 无已售结果: {search_query}")
            return None
        
        # 从结果中提取价格
        prices = []
        sample_items = []
        for item in results[:10]:  # 取前10条
            price_obj = item.get('price', {})
            extracted = price_obj.get('extracted')
            condition = item.get('condition', '')
            
            if extracted:
                try:
                    price_val = float(extracted)
                    title = item.get('title', '')[:80]
                    prices.append(price_val)
                    sample_items.append({
                        'title': title,
                        'price_usd': price_val,
                        'condition': condition,
                    })
                except (ValueError, TypeError):
                    continue
        
        if not prices:
            print(f"  [SerpAPI] 无法提取价格: {search_query}")
            return None
        
        # 计算统计值
        avg_price = sum(prices) / len(prices)
        sorted_prices = sorted(prices)
        median_price = sorted_prices[len(sorted_prices) // 2]
        min_price = sorted_prices[0]
        max_price = sorted_prices[-1]
        
        # 估算对应评级的价格
        # 如果搜索词含PSA/ACE等，结果已按评级过滤
        # 否则需要根据卡牌评级估算
        estimated_grade_price = None
        grade_note = ''
        
        if grader == 'ACE' and grade == 10:
            # ACE 10 ≈ PSA 10 * 0.85
            estimated_grade_price = round(avg_price * 0.85)
            grade_note = f'ACE10=搜索均价(${avg_price:.2f})*0.85=${estimated_grade_price}'
        elif grader == 'PSA' and grade:
            # 尝试从结果中找到对应评级的单品
            grade_str = f'PSA {grade}'
            grade_items = [p for p, item in zip(prices, results[:10]) if grade_str in item.get('title', '').upper()]
            if grade_items:
                estimated_grade_price = round(sum(grade_items) / len(grade_items))
                grade_note = f'PSA{grade}单品均价=${estimated_grade_price} ({len(grade_items)}条)'
            else:
                # 用折扣系数估算
                PSA_DISCOUNT = {10: 1.0, 9: 0.35, 8: 0.18, 7: 0.10, 6: 0.06}
                discount = PSA_DISCOUNT.get(grade, 0.15)
                estimated_grade_price = round(median_price * discount)
                grade_note = f'PSA{grade}估算=中位数(${median_price:.2f})*{discount}=${estimated_grade_price}'
        
        result = {
            'source': 'serpapi.com (eBay sold)',
            'search_query': search_query,
            'results_count': len(results),
            'avg_usd': round(avg_price, 2),
            'median_usd': round(median_price, 2),
            'min_usd': round(min_price, 2),
            'max_usd': round(max_price, 2),
            'samples': sample_items[:3],
        }
        
        if estimated_grade_price:
            result['estimated_grade_price_usd'] = estimated_grade_price
            result['grade_note'] = grade_note
        
        # eBay 主价格 = 评级估算价 or 搜索均价
        result['price_usd'] = estimated_grade_price if estimated_grade_price else round(avg_price, 2)
        
        print(f"  [SerpAPI] {search_query}: {len(prices)}条成交, 均价=${avg_price:.2f}, 中位数=${median_price:.2f}")
        if grade_note:
            print(f"           {grade_note}")
        
        return result
        
    except Exception as e:
        print(f"  [SerpAPI] 错误: {e}")
        return None


def calculate_ace10_estimate(card, prices):
    """
    ACE 10 估值计算逻辑:
    - ACE 是英国评级机构，市场认知度低于 PSA
    - ACE 10 通常介于 PSA 8-9 之间
    - 估值取 PSA 8-9 中间偏上 (约 0.6 * PSA8 + 0.4 * PSA9)
    - 如果只有 PSA 10，则用 PSA 10 * 0.25~0.35 估算 (ACE10约为PSA10的25-35%)
    """
    grading = card.get('grading', {})
    if not grading or grading.get('grader', '').upper() != 'ACE':
        return None
    
    psa10 = prices.get('psa10_usd')
    psa9 = prices.get('psa9_usd')
    psa8 = prices.get('psa8_usd')
    raw = prices.get('raw_usd')
    
    estimate_usd = None
    logic = ''
    
    if psa8 and psa9:
        # 有 PSA 8 和 PSA 9，取中间偏上
        estimate_usd = psa8 + (psa9 - psa8) * 0.6
        logic = f"PSA8(${psa8}) + (PSA9(${psa9})-PSA8)*0.6 = ${estimate_usd:.0f}"
    elif psa9 and psa10:
        # 有 PSA 9 和 PSA 10，PSA8 约为 PSA9 的 0.6-0.7
        est_psa8 = psa9 * 0.65
        estimate_usd = est_psa8 + (psa9 - est_psa8) * 0.6
        logic = f"估算PSA8(${est_psa8:.0f}) + (PSA9(${psa9})-PSA8)*0.6 = ${estimate_usd:.0f}"
    elif psa10:
        # 仅有 PSA 10
        estimate_usd = psa10 * 0.30
        logic = f"PSA10(${psa10}) * 0.30 = ${estimate_usd:.0f}"
    elif psa9:
        # 仅有 PSA 9
        estimate_usd = psa9 * 0.75
        logic = f"PSA9(${psa9}) * 0.75 = ${estimate_usd:.0f}"
    elif psa8:
        # 仅有 PSA 8
        estimate_usd = psa8 * 1.1
        logic = f"PSA8(${psa8}) * 1.10 = ${estimate_usd:.0f}"
    elif raw:
        # 仅有裸卡价
        estimate_usd = raw * 1.5
        logic = f"Raw(${raw}) * 1.50 = ${estimate_usd:.0f}"
    else:
        return None
    
    estimate_cny = round(estimate_usd * USD2CNY)
    print(f"  [ACE10估值] {logic} → ¥{estimate_cny}")
    
    return {
        'ace10_estimated_cny': estimate_cny,
        'ace10_estimated_usd': round(estimate_usd, 2),
        'estimate_logic': logic,
    }


def fetch_xianyu(card):
    """闲鱼搜索 - 社区二手交易价格 (CNY)
    使用 goofish-client Node.js 包，通过子进程调用 xianyu_search.js
    """
    name_en = card.get('name_en', '')
    name_cn = card.get('name_cn', '')
    grading = card.get('grading', {})
    grader = grading.get('grader', '').upper()
    grade = grading.get('grade', '')
    game = card.get('game', '')
    
    # 构造搜索关键词，优先用中文名+评级
    keywords = []
    if grader and grade:
        grade_prefix = f"{grader}{grade}"
    else:
        grade_prefix = ""
    
    # 中文别名映射 (同cardhobby的alt_names)
    alt_names = {
        'Pikachu on the Ball': '足球皮卡丘',
        'Pikachu Futsal': '足球皮卡丘',
    }
    name_cn_alt = alt_names.get(name_en, name_cn)
    
    if name_cn_alt and grade_prefix:
        keywords.append(f"{grade_prefix} {name_cn_alt}")
    if name_cn and grade_prefix and name_cn != name_cn_alt:
        keywords.append(f"{grade_prefix} {name_cn}")
    if name_en and grade_prefix:
        keywords.append(f"{grade_prefix} {name_en}")
    if name_cn_alt:
        keywords.append(name_cn_alt)
    if name_cn and name_cn != name_cn_alt:
        keywords.append(name_cn)
    if name_en:
        keywords.append(name_en)
    
    # 去重并限制 (最多5个关键词，无评级前缀的放后面)
    keywords = list(dict.fromkeys(keywords))[:5]
    
    xianyu_script = Path(__file__).parent / 'xianyu_search.js'
    cookie_path = DATA_DIR / 'xianyu_cookie.json'
    node_path = '/Users/pure/.workbuddy/binaries/node/versions/22.22.2/bin/node'
    node_modules = '/Users/pure/.workbuddy/binaries/node/workspace/node_modules'
    
    if not cookie_path.exists():
        print("  [闲鱼] Cookie文件不存在，跳过")
        return None
    
    if not xianyu_script.exists():
        print("  [闲鱼] xianyu_search.js 不存在，跳过")
        return None
    
    import subprocess
    
    for kw in keywords:
        try:
            env = os.environ.copy()
            env['NODE_PATH'] = node_modules
            result = subprocess.run(
                [node_path, str(xianyu_script), 'search', kw, '--limit', '10'],
                capture_output=True, text=True, timeout=15,
                env=env,
                cwd='/Users/pure/.workbuddy/binaries/node/workspace'
            )
            
            if result.returncode != 0:
                print(f"  [闲鱼] 搜索'{kw}'失败: {result.stderr[:100]}")
                continue
            
            data = json.loads(result.stdout)
            items = data.get('items', [])
            
            if not items:
                print(f"  [闲鱼] '{kw}' 无结果")
                continue
            
            # 过滤: 排除DIY/自制卡、卡组、配件
            valid_items = []
            for item in items:
                title = item.get('title', '')
                price = item.get('price_yuan', 0)
                
                # 排除关键词
                exclude_kw = ['DIY', '自制', '代餐', '打印', '卡组', '卡砖', '保护套', '卡套']
                if any(kw in title for kw in exclude_kw):
                    continue
                
                # 排除价格异常低 (<30元可能是配件/邮费)
                if price < 30:
                    continue
                
                # 排除明显不是单卡的商品 (全套/三件套等)
                if '全套' in title or '三件套' in title or '5张' in title:
                    continue
                
                valid_items.append(item)
            
            if not valid_items:
                print(f"  [闲鱼] '{kw}' 过滤后无有效结果")
                # 仍返回原始结果中最便宜的（用户可自行判断）
                if items:
                    raw_lowest = min(items, key=lambda x: x.get('price_yuan', 0))
                    print(f"  [闲鱼] 最低价(含DIY等): ¥{raw_lowest.get('price_display', '')} - {raw_lowest.get('title', '')[:40]}")
                continue
            
            prices_cny = [it['price_yuan'] for it in valid_items]
            prices_cny.sort()
            
            lowest = prices_cny[0]
            median = prices_cny[len(prices_cny) // 2]
            
            print(f"  [闲鱼] '{kw}' 找到{len(valid_items)}个有效结果, 最低¥{lowest:.0f}, 中位¥{median:.0f}")
            
            return {
                'keyword': kw,
                'lowest_cny': lowest,
                'median_cny': median,
                'count': len(valid_items),
                'items': [{
                    'title': it['title'][:60],
                    'price_cny': it['price_yuan'],
                    'price_display': it.get('price_display', ''),
                    'area': it.get('area', ''),
                    'item_id': it.get('item_id', ''),
                } for it in valid_items[:5]],
            }
            
        except subprocess.TimeoutExpired:
            print(f"  [闲鱼] 搜索'{kw}'超时")
            continue
        except json.JSONDecodeError:
            print(f"  [闲鱼] 搜索'{kw}'返回格式错误")
            continue
        except Exception as e:
            print(f"  [闲鱼] 搜索'{kw}'异常: {e}")
            continue
    
    return None


def main():
    print("=" * 60)
    print("TCG 持仓看板 - 价格更新")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # 备份
    backup_portfolio()
    
    # 加载
    portfolio = load_portfolio()
    cards = portfolio.get('cards', [])
    updated_count = 0
    
    for c in cards:
        name = c.get('name_en') or c.get('name_cn') or ''
        cid = c.get('id', '')
        print(f"\n--- {name} ({cid}) ---")
        
        prices = c.get('current_prices', {})
        if 'sources_detail' not in prices:
            prices['sources_detail'] = {}
        
        # 1. tcgapi.dev
        tcg = fetch_tcgapi(c)
        if tcg and tcg.get('price_usd'):
            prices['tcgapi_usd'] = tcg['price_usd']
            prices['sources_detail']['tcgapi'] = tcg
        
        # 2. SerpAPI (eBay 已售, 所有游戏所有语言)
        serp = fetch_serpapi_ebay(c)
        if serp and serp.get('price_usd'):
            prices['ebay_usd'] = serp['price_usd']
            prices['sources_detail']['serpapi_ebay'] = serp
            # 如果有评级估算价，也更新PSA参考价
            if serp.get('estimated_grade_price_usd'):
                grading = c.get('grading', {})
                grade = grading.get('grade', None)
                grader = grading.get('grader', '').upper()
                if grader == 'PSA' and grade:
                    key = f'psa{grade}_usd'
                    if not prices.get(key):  # 不覆盖已有数据
                        prices[key] = serp['estimated_grade_price_usd']
        
        # 3. TCGPriceLookup (TCGPlayer 裸卡市场价, 多游戏)
        tpl = fetch_tcgpricelookup(c)
        if tpl:
            prices['sources_detail']['tcgpricelookup'] = tpl
            # 如果 tcgapi 没有数据，用 TCGPriceLookup 补充
            if tpl.get('raw_near_mint_market_usd') and not prices.get('tcgapi_usd'):
                prices['tcgapi_usd'] = tpl['raw_near_mint_market_usd']
                print(f"  [TCGPriceLookup] 补充 tcgapi_usd=${tpl['raw_near_mint_market_usd']}")
            # 更新裸卡参考价
            if tpl.get('raw_near_mint_market_usd') and not prices.get('raw_usd'):
                prices['raw_usd'] = tpl['raw_near_mint_market_usd']
        
        # 4. PokemonPriceTracker (PSA评级价+eBay, 仅Pokemon)
        pp = fetch_pokeprice(c)
        if pp:
            prices['sources_detail']['pokeprice'] = pp
            # 更新 pokemonpricetracker_usd（TCGPlayer 裸卡市场价）
            if pp.get('raw_market_usd'):
                prices['pokemonpricetracker_usd'] = pp['raw_market_usd']
            # 更新裸卡参考价（不覆盖已有数据）
            if pp.get('raw_market_usd') and not prices.get('raw_usd'):
                prices['raw_usd'] = pp['raw_market_usd']
            # 保存 TCGPlayer 链接
            if pp.get('tcgplayer_url'):
                prices['sources_detail']['pokeprice']['tcgplayer_url'] = pp['tcgplayer_url']
        
        # 5. PriceCharting (PSA评级参考, 仅Pokemon, fallback)
        if not prices.get('psa10_usd') and c.get('game') == 'pokemon':
            pc = fetch_pricecharting(c)
            if pc:
                if pc.get('psa10_usd'):
                    prices['psa10_usd'] = pc['psa10_usd']
                if pc.get('psa9_usd'):
                    prices['psa9_usd'] = pc['psa9_usd']
                if pc.get('psa8_usd'):
                    prices['psa8_usd'] = pc['psa8_usd']
                if pc.get('raw_usd'):
                    prices['raw_usd'] = pc['raw_usd']
                prices['sources_detail']['pricecharting'] = pc
        
        # 6. 卡淘 CardHobby (CNY价格最可靠的来源！无需登录)
        ch = fetch_cardhobby(c)
        if ch:
            prices['sources_detail']['cardhobby'] = ch
            if ch.get('lowest_cny'):
                prices['cardhobby_cny'] = ch['lowest_cny']
        
        # 7. 镖卡 BiuCard (多源成交价: eBay/Goldin/Heritage/Fanatics/ALT)
        bk = fetch_biaoka(c)
        if bk:
            prices['sources_detail']['biaoka'] = bk
            # 更新镖卡PSA10估值
            if bk.get('psa10_price_cny') and bk['psa10_price_cny'] > 0:
                prices['biaoka_psa10_cny'] = bk['psa10_price_cny']
            # 更新对应评级成交价
            if bk.get('latest_sold_cny'):
                prices['biaoka_latest_sold_cny'] = bk['latest_sold_cny']
                prices['biaoka_latest_sold_grade'] = bk.get('latest_sold_grade', '')
                prices['biaoka_latest_sold_source'] = bk.get('latest_sold_source', '')
            # PSA10参考成交价（当目标评级无数据时）
            if bk.get('psa10_ref_sold_cny'):
                prices['biaoka_psa10_ref_cny'] = bk['psa10_ref_sold_cny']
        
        # 8. 闲鱼 Xianyu (社区二手交易价格, CNY)
        xy = fetch_xianyu(c)
        if xy:
            prices['sources_detail']['xianyu'] = xy
            if xy.get('lowest_cny'):
                prices['xianyu_lowest_cny'] = xy['lowest_cny']
            if xy.get('median_cny'):
                prices['xianyu_median_cny'] = xy['median_cny']
        
        # 9. eBay Browse API (需要 OAuth Token, 通常不可用)
        try:
            from ebay_integration_stub import fetch_ebay_price
            ebay = fetch_ebay_price(name)
            if ebay and ebay.get('median_usd'):
                prices['ebay_usd'] = ebay['median_usd']
                prices['sources_detail']['ebay'] = ebay
        except Exception as e:
            print(f"  [eBay API] 跳过: {e}")
        
        # 10. ACE 10 估值计算
        ace = calculate_ace10_estimate(c, prices)
        if ace:
            prices['ace10_estimated_cny'] = ace['ace10_estimated_cny']
            prices['ace10_estimated_usd'] = ace['ace10_estimated_usd']
            prices['sources_detail']['ace10_estimate'] = ace
        
        # 更新时间戳
        prices['last_updated'] = datetime.now(timezone.utc).isoformat()
        c['current_prices'] = prices
        updated_count += 1
    
    # 更新全局时间戳
    portfolio['last_updated'] = datetime.now(timezone.utc).isoformat()
    
    # 保存
    save_portfolio(portfolio)
    
    print(f"\n{'=' * 60}")
    print(f"更新完成! 共处理 {len(cards)} 张卡牌")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
