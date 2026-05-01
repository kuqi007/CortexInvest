---
name: screenshot-stock-import
description: 从截图中识别股票信息并导入系统。支持持仓截图（更新成本和股数）和自选截图（添加股票到自选列表）
user-invocable: true
---

# 截图股票导入（Skill 入口）

当用户说“导入截图”“识别持仓截图”“把这些自选截图导入系统”等请求时，必须使用本 skill 作为入口。不要让用户手动记 CLI；agent 负责执行命令、展示计划、等待确认后再应用。

本 skill 使用本地 Vision CLI 解析券商/平台截图，生成 `import_plan.json`，再通过工具的 apply 路径写入系统。**禁止**直接编辑 JSON 或 SQLite；配置变更必须走 Web `POST /api/config` 或由本工具的 apply 路径触发。

## 支持的截图类型

- **持仓截图**：识别股票身份（代码或可靠名称）、成本、股数；辅助读取市值、现价、当日盈亏。
- **自选截图**：识别股票身份（代码或可靠名称），添加为自选/关注。
- 已适配：同花顺持仓/自选（暗色、浅色）、东方财富持仓/自选、香港熊猫持仓/自选。

## 用户触发方式

用户只需要这样说：

```text
帮我导入 ~/Downloads/hold20260430/test1.png
```

或：

```text
把 ~/Downloads/hold20260430 里的截图都识别一下，确认后导入
```

agent 必须按下面流程执行。

## 标准流程

### 1. 隐私提示

先提醒用户：截图会发送给视觉模型服务商，可能包含真实持仓、成本、股数等敏感信息。用户继续要求导入时视为同意。

### 2. 干跑生成计划

单张截图：

```bash
uv run python src/tools/screenshot_stock_import.py \
  --dry-run "<image-path>" \
  --provider glm \
  --output-json ".screenshot_import_runs/<name>.plan.json"
```

多张截图：

```bash
for f in <folder>/*.{png,jpg,jpeg}; do
  [ -e "$f" ] || continue
  uv run python src/tools/screenshot_stock_import.py \
    --dry-run "$f" \
    --provider glm \
    --output-json ".screenshot_import_runs/$(basename "$f").plan.json"
done
```

优先使用 `glm`。若用户明确要求其他 provider，可用 `--provider kimi` 或 `--provider auto`。

### 3. 展示识别计划（必须全文列出后等你确认）

干跑结束后，**必须把本次导出的识别结果全部展示给用户**，**不能只报数字摘要**。用户核对无误并明确同意后，才能进入第 4 步 apply。

对 **每一个** 生成的 `*.plan.json`：

1. 标明 **源截图文件名** 与 **plan 路径**。
2. **`auto_apply`**：**逐条**列出每条计划的 **`code`、`name`**；持仓还须 **`cost`、`shares`**（及 `payload.action` 若为 update）；自选列出 **`type`**（holding/watching）。
3. **`needs_confirmation`**：**逐条**列出同上字段，并列出 **`reason_codes`**。
4. **`rejected`**：**逐条**列出截图识别到的 **`name`**（或 row 内可用字段）及 **`reason_codes`**。

多张截图 = 多个 plan 时，按文件顺序 **每张 plan 都完整列一遍**，避免遗漏。

若条目过多导致单次回复超出合理长度，可 **按 plan 文件分批发送**，并明确「当前为第 N/M 批」，但 **同一 plan 内仍不得省略行级明细**（不可用「等其余 X 条」代替）。

确认话术示例：展示完全部明细后，询问「是否按上述计划执行导入？」——用户回复同意（如「确认 apply」「全部导入」）后，才执行 apply。

**核对要点**：持仓核对股票身份、成本、股数；自选核对股票身份；若有 `rejected` 或异常成本/股数，提醒用户这是截图识别结果，需自行判断是否信任。

### 4. 用户确认后应用

只有用户在看完 **第 3 步全文明细** 并 **明确同意** 后，才能应用：

```bash
uv run python src/tools/screenshot_stock_import.py \
  --apply \
  --plan ".screenshot_import_runs/<name>.plan.json" \
  --yes
```

若用户只让“识别一下”，不要 apply。

## 重要参数

- `--provider glm`：推荐，使用智谱/GLM 视觉模型。
- `--provider auto`：按环境变量自动选择，优先 GLM。
- `--output-json PATH`：保存识别计划。
- `--debug-dir PATH`：保存分类、prompt、provider response 等调试文件。
- `--debug-sensitive`：保存完整敏感调试内容，仅限用户明确要求。
- `--no-external-name-lookup`：不调用 MX API 查缺失代码，只用已有 watchlist 与 `stocks/catalog.json`。
- `--allow-path PATH`：当输入/输出不在默认允许目录时，显式放行路径。

## 识别与安全规则

- 代码优先；代码不可靠时用名称匹配已有 watchlist、`stocks/catalog.json`，必要时走 MX API 查码。
- 持仓截图必要字段是：股票身份 + 成本 + 股数。
- 自选截图必要字段是：股票身份。
- 不确定、异常、按名称补全的行进入 `needs_confirmation`，不得静默自动导入。
- 板块/指数/概念等非股票项不要伪造成股票代码。
- API key 必须来自环境变量，不能写入代码或日志。

## 环境要求

需要 `.env` 中至少配置一个视觉模型 key，推荐：

```bash
ZHIPU_API_KEY=...
```

或：

```bash
GLM_API_KEY=...
```

若要按名称补缺失代码，需配置：

```bash
MX_APIKEY=...
```
