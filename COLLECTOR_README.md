# 价格采集器说明

本项目的 v1 采集路线是：先用直接 API client，不引入 Scrapy。Scrapy 适合后续做队列、去重、限速和大规模抓取；当前持仓规模较小，真正难点是平台登录态、签名、`raw_data` 包装和价格字段归一化。

## 运行方式

首次 clone 后，如果要用真实持仓运行采集器，先复制 `data/portfolio.example.json` 为 `data/portfolio.json`，再填自己的卡牌。真实 `portfolio.json` 已在 `.gitignore` 中忽略，避免把持仓、成本和价格快照提交到 GitHub。

```bash
python scripts/collect_card_prices.py --dry-run --limit 2
python scripts/collect_card_prices.py
```

- `--dry-run`：只请求和汇总，不写入 `portfolio.json` 或价格历史。
- `--no-live`：不访问外部 API，只输出本地/抓包审计状态。
- `--no-fallback`：主来源没价格时，不调用卡淘、TCGPriceLookup、PokePrice、SerpAPI/eBay 兜底。
- `--limit N`：只处理前 N 张卡。
- `--card-id ID`：只处理指定卡，可重复传入多个。

正式运行会写入：

- `data/portfolio.json`：本地真实持仓，每张卡的 `current_prices.collector_sources` 会被更新。该文件不会提交到 Git。
- `data/portfolio.example.json`：公开示例数据，供页面 fallback 和新用户参考。
- `data/price_history/YYYY-MM-DD.json`：当天价格快照。
- `data/collector_runs/YYYYMMDDTHHMMSS.json`：完整采集结果和状态。
- `data/backups/portfolio.json.collector.*.bak`：运行前备份。

走势点标准字段是 `time` 和 `value`：看板用 `time` 作为 x 轴，用 `value` 作为 y 轴。旧字段 `price_cny` 会继续保留，方便兼容已有数据。

## 私密配置

真实 token/API key 放在 `data/api_tokens.json` 或环境变量里，不写入源码。格式参考 `data/api_tokens.example.json`。

支持的环境变量：

- `BIAOKA_TOKEN`
- `JIHUANSHE_TOKEN`
- `TCGAPI_KEY`
- `SERPAPI_KEY`
- `TCGPRICELOOKUP_KEY`
- `POKEPRICE_KEY`
- `PRICECHARTING_TOKEN`

## 状态码

- `ok`：拿到可用价格。
- `auth_required`：缺少平台 token 或 token 失效。
- `raw_data_unresolved`：接口返回 `raw_data`，暂未证明可稳定解码。
- `no_match`：搜索无可靠匹配。
- `no_price`：匹配到卡，但没有可用价格/成交记录。
- `rate_limited`：平台限流。
- `skipped`：当前 v1 范围外，例如非宝可梦或集换社只处理日版。
- `error`：网络、接口或未知错误。

## 当前结论

- 镖卡：已能稳定作为美版宝可梦主数据源，输出当前价、走势点、近期成交。
- 镖卡详情接口：采集器会调用 card detail，并在 `raw_ref.detail_keys/detail_trend_points` 里记录详情响应可用性。
- 日版路线：日版卡会额外输出 `pricecharting_jp` 和 `jp_market_reference`。`pricecharting_jp` 需要 `PRICECHARTING_TOKEN` 才能自动取当前价；`jp_market_reference` 会给 Aucfan、Mercari JP、Yahoo!オークション、magi、SNKRDUNK、CardRush、PriceCharting 等人工核验入口。
- 第三方 fallback：只有主来源没有价格时才触发，避免无谓请求；结果同样写入 `collector_sources`。
- 集换社：已有 endpoint 和抓包样本，但日版价格尚未归一化；采集器会在 run 文件的 `jihuanshe_raw_data_audit` 中记录 `price-history/products/get-base-info` 等 raw_data 样本的 endpoint、长度和指纹，方便下一步专门研究解码。
- 看板：详情弹窗会按日版参考、美版参考、国内参考分组显示来源，避免不同市场的信息混在一起；采集器详情会显示来源、状态、置信度、时间/value 走势轴图和近期成交。
