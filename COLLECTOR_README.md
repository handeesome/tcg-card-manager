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
- `permission_denied`：token 有效，但账号没有集换社市场权限或实名/风控状态不满足接口要求。
- `app_upgrade_required`：命中了旧 App/API 路径，服务端要求升级客户端或使用新版加密请求层。
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
- 集换社：3.42.3 App 的卡详情分享页会请求 `cardDetail_wxCover?...&price=<CNY>`，分析器/归一化器已能把 URL query 中的 `price` 转成 `collector_sources` 当前价；旧 `card-versions`/`sellers/products` 等 API 仍会返回权限拒绝或升级提示。
- 看板：详情弹窗会按日版参考、美版参考、国内参考分组显示来源，避免不同市场的信息混在一起；采集器详情会显示来源、状态、置信度、时间/value 走势轴图和近期成交。

## 集换社专项探测

当前集换社路线只做授权账号下的个人采集研究，不尝试绕过权限或风控。先用探针确认登录态和 endpoint 状态：

```bash
python scripts/probe_jihuanshe.py
python scripts/probe_jihuanshe.py --query "ポケるんTVのピカチュウと仲間たち S-P"
python scripts/extract_jihuanshe_endpoints.py
python scripts/probe_jihuanshe.py --endpoints-file data/collector_runs/jihuanshe_endpoint_discovery/<file>.json --limit 15
```

探针结果写入 `data/collector_runs/jihuanshe_probe/`，不会提交到 Git。当前已知结果：

- 公共 `users` 接口可达。
- `card-versions`、`card-versions/search`、`products` 在当前 token 下返回 `MARKET_PERMISSION_DENY`。
- `sellers/products`、`entrustedProduct/cardVersionPrices`、`get-base-info` 命中旧路径时返回 `SYSTEM_UPGRADED`。
- APK 3.23.15 自动抽取到 26 个集换社 endpoint；当前 token 小批量探测中只有 `articles?type=entrusted_buy` 和 `articles?type=condition` 可达，这两类是说明文章，不是价格数据。
- APK 3.23.15 中存在 `crypto.dart`、`kEncryptAPIRequest`、`x-raw-data`、`x-raw-headers`、`HybridCommonHostApi.encrypt`，说明新版 App 可能走加密请求/响应层；需要成功抓包样本来确认字段。
- 3.42.3 未登录态点击 Web 热榜卡牌会落到登录页，但跳转前会加载 `uat.jihuanshe.com/app/cardDetail_wxCover`。该 URL 已实测携带 `image`、`name_cn`、`rarity`、`pack_name_cn` 和 `price`，例如皮卡丘样本 `price=150.84`。

如果拿到自己账号的成功抓包，先导入并脱敏：

最短路径是一条 pipeline 命令：

```bash
python scripts/run_jihuanshe_pipeline.py path/to/capture.har --keep-raw-data --card-id <portfolio-card-id>
```

它会生成 `analysis.json`、`normalized.json` 和 `portfolio.preview.json` 到 `data/collector_runs/jihuanshe_pipeline/<timestamp>/`。确认预览无误后，可以把同一命令加上 `--write` 写回真实持仓。

也可以拆开逐步调试：

```bash
python scripts/import_jihuanshe_capture.py path/to/capture.har
python scripts/import_jihuanshe_capture.py --keep-raw-data path/to/capture.json
python scripts/analyze_jihuanshe_captures.py
python scripts/normalize_jihuanshe_captures.py
python scripts/apply_jihuanshe_normalized.py data/collector_runs/jihuanshe_capture_normalized/<file>.json --card-id <portfolio-card-id> --out data/portfolio.preview.json
```

抓包优先级：

- 搜索卡：`card-versions/search`。
- 卡详情或基础信息：`card-versions/*`、`get-base-info`。
- 商品/寄售列表：`products`、`sellers/products`、`entrustedProduct/cardVersionPrices`。
- 走势：`price-history`。

导入器会保存到 `scripts/captured_requests/`，并自动脱敏 `Authorization`、`Cookie`、token、手机号、邮箱等字段。若响应只有 `raw_data`，需要加 `--keep-raw-data` 才会保留原文用于解码；否则只记录长度、前缀和指纹。

分析器会读取 `scripts/captured_requests/*.json`，输出到 `data/collector_runs/jihuanshe_capture_analysis/`，并汇总：

- `price_candidate_count`：普通 JSON 或请求 URL query 中可疑价格字段数量。
- `trend_candidate_count`：同时包含时间字段和价格字段的走势数组数量。
- `raw_data_files`：仍需解码的 `raw_data` 响应文件。

归一化器会读取同一批抓包样本，输出到 `data/collector_runs/jihuanshe_capture_normalized/`。普通商品/成交 JSON 会转成 `price_current`；`cardDetail_wxCover` 的 URL query `price` 会转成 `card_detail_reference`；走势 JSON 会转成 `price_history`，并用最后一个走势点作为 `trend_latest` 当前参考价。输出结构与采集器的 `collector_sources` 兼容，确认字段稳定后即可接入正式持仓更新。

应用器会把多条集换社归一化结果合并成单个 `collector_sources` 条目：优先使用成交/商品当前价，同时保留最长走势。默认只写 `--out` 指定的预览文件；确认无误后再加 `--write` 覆盖真实 `data/portfolio.json`，脚本会先创建 `.jihuanshe.*.bak` 备份。

如果使用 mitmproxy，可以让脚本自动捕获并脱敏集换社流量：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_jihuanshe_mitm.ps1 -Port 8080 -KeepRawData
```

需要本机已安装 `mitmproxy`/`mitmdump`。然后在手机 Wi-Fi 里把代理设成这台电脑的 IP 和端口 `8080`，安装 mitmproxy CA 证书，打开集换社 App，手动搜索/打开目标卡牌市场页。捕获结果会直接写入 `scripts/captured_requests/`，之后运行：

在 MuMu 12 模拟器里测试时，已验证可用的链路是：

1. 新建普通实例，不用 mini 实例。
2. 安装 `集换社_3.42.3_yyb.apk`。如果需要复现 MOP runtime 初始化，也可以先装 `集换社_3.23.15_apkcombo.com.apk`，再覆盖安装 3.42.3。
3. 启动 mitmproxy，脚本会监听 `0.0.0.0:8080` 并允许模拟器连接。
4. 模拟器代理设置为 `10.0.2.2:8080`。
5. 临时开启 MuMu `root_permission=true` 和 `system_disk_readonly=false`，把 mitmproxy CA 按 Android 系统证书名写入 `/system/etc/security/cacerts/`。
6. 关回 `root_permission=false` 并重启模拟器，再启动集换社；否则 App 可能在启动阶段退出。

当前实测 3.23.15 首次启动会先请求 `applet.jihuanshe.com` 和 `applet-resource.jihuanshe.com` 的 MOP/FinClip runtime 包。3.42.3 可在 root 关闭后稳定启动，并能通过首页搜索页热榜触发卡详情分享页价格。业务页需要用户确认隐私弹窗后继续抓取。mitm 插件会把二进制响应完整保存到 `scripts/captured_requests/_bodies/`，JSON 里只记录文件名、路径、长度、sha256 和 content-type，避免小程序包或图片响应被截断。

```bash
python scripts/analyze_jihuanshe_captures.py
python scripts/normalize_jihuanshe_captures.py
python scripts/apply_jihuanshe_normalized.py data/collector_runs/jihuanshe_capture_normalized/<file>.json --card-id <portfolio-card-id> --out data/portfolio.preview.json
```
