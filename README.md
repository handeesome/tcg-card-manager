# TCG Card Manager

一个本地优先的 TCG 卡牌持仓管理与价格追踪看板。当前重点是宝可梦卡牌：记录持仓、买入成本、评级信息和多来源价格参考，并通过本地网页看板查看盈亏、价格来源和走势。

## 功能

- 本地持仓看板：按卡牌、游戏、语言、评级和收益状态浏览收藏。
- 价格追踪：统一记录国内、美版、日版等市场来源的当前价、走势点和成交参考。
- 本地编辑：通过本地服务保存 `data/portfolio.json`。
- 价格采集：使用直接 API client 更新持仓价格，并生成运行记录、历史快照和备份。
- 隐私保护：真实持仓、token、cookie、采集输出和备份默认不提交到 Git。

## 快速开始

```bash
python server.py
```

然后打开：

```text
http://localhost:8765/web/
```

如果本地还没有真实持仓，页面会使用 `data/portfolio.example.json` 作为示例数据。

## 使用真实持仓

复制示例文件并填入自己的卡牌数据：

```bash
cp data/portfolio.example.json data/portfolio.json
```

`data/portfolio.json` 已被 `.gitignore` 忽略，适合存放真实持仓、成本和价格数据。

## 价格采集

采集器说明见 [COLLECTOR_README.md](COLLECTOR_README.md)。

常用命令：

```bash
python scripts/collect_card_prices.py --dry-run --limit 2
python scripts/collect_card_prices.py
```

真实 token 或 API key 可以放在 `data/api_tokens.json`，格式参考 `data/api_tokens.example.json`。这些私密配置不会提交到 Git。

## 目录结构

```text
data/
  api_tokens.example.json
  portfolio.example.json
scripts/
  collect_card_prices.py
  chinese_platform_api.py
web/
  index.html
server.py
COLLECTOR_README.md
```

## 免责声明

价格数据仅供个人记录和参考，不构成投资建议。不同市场、语言版本、评级机构和成交状态会导致价格差异，重要决策请自行核验来源。
