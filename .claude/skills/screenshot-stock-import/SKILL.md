---
name: screenshot-stock-import
description: 从截图中识别股票信息并导入系统。支持持仓截图（更新成本和股数）和自选截图（添加股票到自选列表）
user-invocable: true
---

# 截图股票导入（Vision CLI）

使用本地 Vision CLI 从券商/平台截图解析股票，生成 `import_plan.json`，再经 `--apply` 写入系统。**禁止**直接编辑 JSON 或 SQLite；配置变更必须走 Web `POST /api/config` 或由本工具的 `--apply` 路径触发。

## 隐私与安全

- 视觉识别会把截图发往所选模型服务商，截图中可能包含**真实持仓、成本、股数**等敏感信息；使用前请确认可接受该服务商的处理方式。
- **调试输出默认关闭。** Tier A 调试负载仅包含**脱敏**字段（如 `sha256:` 代码哈希、汇总计数、置信度统计），**不含**原始代码、名称、成本、股数、绝对路径或原始图片字节。
- 如需记录**完整**服务商响应等敏感内容，必须显式使用 **`--debug-sensitive`**（仅限受控环境）。

## 支持的截图类型

- **持仓截图**：识别代码、名称、成本、股数 → 计划更新持仓。
- **自选截图**：识别代码、名称 → 计划添加自选等。

## 推荐工作流

### 1. 干跑生成计划

```bash
uv run python -m src.tools.screenshot_stock_import <image-path> --provider auto --dry-run
```

### 2. 审查 `import_plan.json`

在运行目录下的 `.screenshot_import_runs/<import_run_id>/import_plan.json` 中查看分组：

- `auto_apply` — 建议自动执行的动作
- `needs_confirmation` — 需人工确认
- `rejected` — 拒绝或无法应用

### 3. 券商 / 平台不明确时

若分类置信度低或平台识别不准，**询问用户**后带上明确参数重跑，例如：

```bash
uv run python -m src.tools.screenshot_stock_import <image-path> \
  --platform eastmoney --type holding --dry-run
```

（按实际截图替换 `--platform`、`--type`：`holding` / `watchlist` 等。）

### 4. 应用计划（仅确认后）

```bash
uv run python -m src.tools.screenshot_stock_import --apply \
  --plan .screenshot_import_runs/<import_run_id>/import_plan.json --yes
```

将 `<import_run_id>` 替换为上一步干跑输出中的目录名。

## 规则摘要

- 配置与持仓的权威源是 **API / DB**；不要为「省事」手改 `monitor_config` 或 SQLite。
- 识别与合并以 CLI + `import_plan.json` 为准；不要用多子 agent 各读一遍截图的旧流程代替本工具。

## 识别规则（参考）

- **匹配优先级**：优先股票代码；无代码时再靠名称对齐系统中已有标的。
- **代码格式**：A 股 6 位；港股 `HK` + 5 位数字；美股字母代码等。

## 注意事项

- 截图清晰度直接影响识别率。
- 持仓截图应包含可辨的成本与数量列（若平台布局特殊，可配合 `--platform` 指定）。
