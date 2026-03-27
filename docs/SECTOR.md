# 板块指数

## 概览

自定义板块指数 + 主线行情告警。板块轮动功能已归档（前端页面删除，DB 数据保留）。

## Tag-Based Index 架构

自定义指数从 `monitor_watchlist.tags` 自动聚合。给股票打标签（如 `磷化工`）即自动创建/加入该标签的指数。`tag_meta` 表存储每个标签的元数据（star/watch/baseline_value）。

## 数据流

```
[每日 15:30 cron，自动跳过周末]
sector_index_engine.py
  ├── _load_tag_indices() → 从 monitor_watchlist.tags + tag_meta 聚合指数定义
  ├── akshare stock_zh_a_hist() → 成分股日线（EM push2）
  │   └── fallback: 腾讯财经 web.ifzq.gtimg.cn
  ├── 腾讯 qt API → 成分股名称（batch 获取，进程内缓存）
  ├── 新浪财经 API → 板块排名（GBK 解码）
  │   ├── 行业板块: newSinaHy.php (49 板块)
  │   └── 证监会行业: newFLJK.php (84 板块)
  ├── 等权平均涨跌幅 → 自定义指数值
  ├── 日收益率线性回归 + R² 过滤 + 累涨检测 → 主线告警
  └── 写入 sim_trading.db: sector_rotation / sector_daily / sector_alerts
          ↓
/api/sector (Next.js, GET 只读 + POST 管理)
  ├── indices: 从 tag_meta + monitor_watchlist.tags 聚合，含 30d history
  ├── components: 含 name/close/change_pct
  └── alerts: 主线/接近主线告警
```

## 主线检测规则

- 规则: `累涨 >= 8%` AND `日收益率回归斜率 >= 0.05` AND `R² >= 0.4`
- 斜率归一化: 对日收益率%序列做线性回归
- R² 过滤: R²<0.4 说明波动大，不是稳定趋势
- 接近告警: `累涨 >= 75%阈值` AND `slope > 0`
- watch 过滤: `watch=false` 的指数跳过检测
- `min_days_since_create (默认3天)` 排除一日游

## 交易日检测

`_is_trading_day()` 检查周一至周五。周末运行 `--indices`/`--rotation`/`--detect` 自动跳过，不产生脏数据。

## 运行方式

```bash
uv run python -m src.tools.sector_index_engine              # 全量运行
uv run python -m src.tools.sector_index_engine --rotation    # 仅采集板块排名
uv run python -m src.tools.sector_index_engine --indices     # 仅计算自定义指数
uv run python -m src.tools.sector_index_engine --backfill ID # 回填指数30天历史
uv run python -m src.tools.sector_index_engine --detect      # 仅检测主线信号
```

## API

- `GET /api/sector` → indices + alerts + rotation
- `GET /api/sector?category=industry&sort=change_pct&top_n=10` → 轮动矩阵
- `POST /api/sector` → create/update/delete/watch/star/config

## SQLite 表

| 表 | 说明 | 保留 |
|---|------|------|
| `sector_rotation` | 板块每日排名 | 90 天 |
| `sector_daily` | 自定义指数日线 | 180 天 |
| `sector_alerts` | 主线告警 | 30 天 |
