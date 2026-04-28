---
name: model-router
description: Use when checking AI provider quota, 5-hour or weekly limits, model availability, opencode slim preset routing, or switching between kimi, glm, and minimax presets.
---

# Model Router Skill

## 触发语
- "查看模型额度"
- "切换模型到 minimax"
- "kimi 额度快用完了"
- "glm 额度快用完了"
- "自动选择最优模型"
- "model router status"
- "切换 preset"

## 使用方式

### 1. 查看当前状态（推荐）
```bash
python3 scripts/model_router.py status
```
输出示例：
```
📋 当前 preset: balanced
--------------------------------------------------
🟢 kimi        [ok]  HTTP 200
🟢 glm         [ok]  HTTP 200
🟢 minimax     [ok]  HTTP 200
--------------------------------------------------
💡 推荐 preset: balanced
```

状态图标含义：
- 🟢 ok — API 正常，可用
- 🟡 warning — 可用但响应包含额度警告
- 🔴 quota_exhausted — 额度已耗尽（429/403 + 额度错误）
- 🟠 rate_limited — 频率限制（可能临时）
- ⚫ error — 网络或其他错误
- ⚪ no_key — 未配置 API key

`status` 当前是**可用性探测**，不是精确用量查询。需要看 5 小时/每周额度时，按下面的“额度查询”执行。

### 1.5 查看剩余额度
```bash
python3 scripts/model_router.py quota
```
输出 Kimi / GLM 的 5 小时和每周额度，以及 MiniMax 的 5 小时额度；不打印 API key。Kimi 使用 opencode 配置里的 `kimi` key，该 key 必须能访问 `api.kimi.com/coding/v1/usages`。命令末尾会根据额度推荐 preset。

### 2. 自动选择最优 preset
```bash
python3 scripts/model_router.py auto
```
自动切换规则（优先级从高到低）：
1. **balanced** — GLM 5 小时和周额度都 ≥ 50% 时使用
2. **economy** — GLM 任一额度 < 50% 时切 MiniMax 主力（前提是 MiniMax 5 小时额度可用）
3. **smart** — Kimi oracle，通常手动切换；只有前两者不可用时兜底

### 3. 手动切换 preset
```bash
python3 scripts/model_router.py switch economy
python3 scripts/model_router.py switch balanced
python3 scripts/model_router.py switch smart
```

兼容 provider 别名：`minimax -> economy`，`glm -> balanced`，`kimi -> smart`。

切换后会自动备份原配置到 `~/.config/opencode/oh-my-opencode-slim.jsonc.bak`。脚本会移除当前环境不支持的顶层 `council` 配置块，避免 `Agent not found: "council"`。

## 额度查询（5小时 / 每周）

优先级：先查 provider 的用量 API；拿不到精确 quota 时，再用 `status` 的 1-token probe 判断是否已耗尽。

| Provider | 5小时额度 | 每周额度 | 推荐查询方式 |
|----------|-----------|----------|--------------|
| Kimi Code | ✅ | ✅ | Kimi Code `/usage` 或 `GET https://api.kimi.com/coding/v1/usages`（OAuth token） |
| GLM Coding Plan | ✅ | ✅ | `GET https://open.bigmodel.cn/api/monitor/usage/quota/limit` |
| MiniMax Token Plan | ✅ | 无 | `GET https://api.minimaxi.com/v1/token_plan/remains`（Token Plan key；字段名有坑） |

### Kimi Code

Kimi OpenPlatform 的余额接口只返回现金/赠金余额：

```bash
curl https://api.moonshot.ai/v1/users/me/balance \
  -H "Authorization: Bearer $MOONSHOT_API_KEY"
```

这不是 Kimi Code 的 5小时/每周 coding quota。Kimi Code quota 应使用 Kimi Code 登录态或 OAuth token：

```bash
# 如果已登录 Kimi CLI，优先进入 Kimi Code shell 后执行
kimi
/usage

# 程序化查询（需要 Kimi Code OAuth access token，不是普通 Moonshot API key）
curl https://api.kimi.com/coding/v1/usages \
  -H "Authorization: Bearer $KIMI_CODE_ACCESS_TOKEN"
```

响应通常包含：
- `usage` / `detail`：weekly quota，字段含 `limit`、`used`、`remaining`、`resetTime`
- `limits[]`：窗口额度；`window.duration=300` + `TIME_UNIT_MINUTE` 表示 5 小时窗口

旧版网页登录态也可能通过 `POST https://www.kimi.com/apiv2/kimi.gateway.billing.v1.BillingService/GetUsages` 获取同类数据，但需要 `kimi-auth` cookie，不建议脚本默认依赖。

### GLM Coding Plan

国内版：

```bash
curl https://open.bigmodel.cn/api/monitor/usage/quota/limit \
  -H "Authorization: $GLM_API_KEY" \
  -H "Content-Type: application/json"
```

国际版 Z.ai：

```bash
curl https://api.z.ai/api/monitor/usage/quota/limit \
  -H "Authorization: $ZAI_API_KEY" \
  -H "Content-Type: application/json"
```

解析规则：
- `data.limits[]` 中 `type == "TOKENS_LIMIT"` 是 coding quota
- `unit=3, number=5` 表示 5 小时额度
- `unit=6, number=1` 表示每周额度
- 新套餐通常有两个 `TOKENS_LIMIT`；老套餐可能只有 5 小时额度
- `percentage` 是已用百分比；`remaining` 若存在则是剩余量；`nextResetTime` 是毫秒时间戳

### MiniMax Token Plan

MiniMax 文档写的是 `www.minimax.io`，但实际更稳定返回 `model_remains` 的是 `api.minimaxi.com`（`api.minimax.chat` 也可）：

```bash
curl https://api.minimaxi.com/v1/token_plan/remains \
  -H "Authorization: Bearer $MINIMAX_API_KEY" \
  -H "Content-Type: application/json"
```

注意：公开 issue 里反馈字段命名容易误导：
- `current_interval_usage_count` 实际按剩余 5 小时请求数理解
- `current_interval_total_count` 是 5 小时总额度
- 已用量可按 `total - remaining` 计算
- MiniMax 当前不展示周额度；脚本只输出 5 小时窗口

MiniMax 只有 Token Plan key 才适合查这个接口；普通 pay-as-you-go key 更偏余额/计费，不一定有 5小时/每周 quota。

## 额度管理策略

当前项目 `oh-my-opencode-slim.jsonc` 的 preset 分配策略：

| Preset | 主力模型 | 用途 |
|--------|----------|------|
| economy | MiniMax | 省 GLM/Kimi 额度，适合日常低成本任务 |
| balanced | GLM | 默认均衡选择 |
| smart | GLM + Kimi oracle | 复杂任务/架构判断，谨慎使用 Kimi |

**当前状态**不要写死在 skill 里；用下面命令实时查看：

```bash
python3 scripts/model_router.py status
```

**建议**：
- GLM 5小时和周额度都 ≥ 50% 时用 `balanced`
- GLM 任一额度 < 50% 时切 `economy`
- Kimi 只在需要 oracle 能力时切 `smart`

## 额度耗尽检测

脚本通过轻量级 API probe（1 token 请求）检测额度状态：

- **kimi**: Coding API 有访问控制，脚本已内置 `claude-code` User-Agent
- **GLM**: 检测 429 + 错误码 1304/1308/1310（额度用完）
- **MiniMax**: 检测 429/403 额度错误

> ⚠️ 注意：`status` 只判断可用性；精确额度需调用上面的 quota API。若 API key 类型不匹配（例如 Kimi Code OAuth vs Moonshot API key、MiniMax Token Plan key vs 普通 Open Platform key），只能 fallback 到 probe 或 Dashboard。
> Dashboard：
> - [Kimi 控制台](https://platform.kimi.com/console/api-keys)
> - [智谱控制台](https://bigmodel.cn/usercenter/proj-mgmt/apikeys)
> - [MiniMax 控制台](https://www.minimaxi.com/user-center/basic-information)

## 自动化（可选）

可在 crontab 中定期执行自动检测：
```bash
# 每 30 分钟检测一次，自动切换
*/30 * * * * cd /Users/zhul1/Documents/dev/openWorkspace/ai-investor && python3 scripts/model_router.py auto >> /tmp/model_router.log 2>&1
```
