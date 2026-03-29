# 飞书通知集成设计

## 目标

在现有 macOS 通知基础上，新增飞书作为并行通知通道。告警发生时同时发送到 macOS 本地弹窗和飞书个人消息。

## 架构

```
stealth_dispatch → notify()
                    ├── macOS: terminal-notifier / osascript（现有）
                    └── 飞书: POST 飞书 API（新增，并行）
```

- macOS 通知保留不变，用于本地弹窗
- 飞书通知新增，用于远程/移动端推送
- 两通道互不影响，独立发送

## 凭证

| 字段 | 值 |
|------|---|
| App ID | `cli_a93c2db9a4b89bef` |
| App Secret | `3tBNzFxifw9ekDSQgBFB0Cdwh1Rm3CwF` |
| 接收人 open_id | `ou_553029ec877f28bdf3217b38bef62c8f` |

## 实现

### 新增文件

无新文件，在现有 `stock_monitor.py` 中新增函数。

### 改动文件

1. **`src/tools/stock_monitor.py`**
   - 新增 `feishu_get_token()` — 获取/缓存 access token（有效期 2h）
   - 新增 `feishu_send(open_id, title, message)` — 发送飞书消息卡片
   - `notify()` 函数末尾追加 `feishu_send()` 调用（失败不影响 macOS 通知）

2. **`src/tools/stock_notifier.py`**
   - `stealth_dispatch` — 在每次 `notify()` 调用后追加 `feishu_send()`
   - `stealth_dispatch_open_close` — 同上

### 飞书消息格式

使用飞书 interactive card，消息内容复用 `_stealth` 字段（伪装格式）：

```
【告警】个股异动
---
腾讯控股 HK.00700
+5.2% | 现价: HK$485.2
触发: L1 | 4分钟前
```

### 错误处理

- 飞书 API 调用失败 → 记录 warning log，继续执行（不阻塞 macOS 通知）
- access token 过期 → 自动重新获取

## 测试计划

1. 本地手动调用 `feishu_send()` 确认消息能收到
2. 启动 monitor.sh，确认告警时飞书和 macOS 同时收到
