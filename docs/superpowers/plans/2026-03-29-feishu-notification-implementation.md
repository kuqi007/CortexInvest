# 飞书通知集成实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 macOS 通知基础上新增飞书点对点消息通道，告警时并行发送。

**Architecture:** `notify()` 在现有 macOS 通知后调用新增的 `feishu_send()`，两者互不阻塞。飞书 API 调用失败不影响 macOS 通知。

**Tech Stack:** `requests`（已在 `stock_monitor.py` 中 import），飞书 Open Platform API。

---

## 凭证

| 字段 | 值 |
|------|---|
| App ID | `cli_a93c2db9a4b89bef` |
| App Secret | `3tBNzFxifw9ekDSQgBFB0Cdwh1Rm3CwF` |
| 接收人 open_id | `ou_553029ec877f28bdf3217b38bef62c8f` |

---

## 任务总览

| # | 任务 | 文件 |
|---|------|------|
| 1 | 新增 `feishu_get_token()` + `feishu_send()` 函数 | `src/tools/stock_monitor.py` |
| 2 | 在 `notify()` 末尾追加飞书发送调用 | `src/tools/stock_monitor.py` |
| 3 | 手动测试飞书通知发送 | - |
| 4 | 提交代码 | git |

---

## Task 1: 新增飞书函数

**文件:** `src/tools/stock_monitor.py`（在 `notify()` 函数之后、AlertEngine 类之前插入）

**凭证常量（追加到文件顶部 DEFAULT_SETTINGS 附近）:**
```python
# ── 飞书配置 ──
FEISHU_APP_ID = "cli_a93c2db9a4b89bef"
FEISHU_APP_SECRET = "3tBNzFxifw9ekDSQgBFB0Cdwh1Rm3CwF"
FEISHU_USER_OPEN_ID = "ou_553029ec877f28bdf3217b38bef62c8f"
_feishu_access_token: str | None = None
_feishu_token_expires_at: float = 0
```

**新增函数（插入位置：第 906 行 notify 函数结束后）:**

```python
# ══════════════════════════════════════════
# 3. 飞书通知
# ══════════════════════════════════════════

def feishu_get_token() -> str | None:
    """获取飞书 access token，带缓存（有效期 2 小时）"""
    global _feishu_access_token, _feishu_token_expires_at
    if _feishu_access_token and time.time() < _feishu_token_expires_at - 60:
        return _feishu_access_token
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    try:
        resp = requests.post(url, json={
            "app_id": FEISHU_APP_ID,
            "app_secret": FEISHU_APP_SECRET,
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            logger.warning(f"飞书 token 获取失败: {data}")
            return None
        _feishu_access_token = data["tenant_access_token"]
        _feishu_token_expires_at = time.time() + data.get("expire", 7200)
        return _feishu_access_token
    except Exception as e:
        logger.warning(f"飞书 token 请求异常: {e}")
        return None


def feishu_send(title: str, message: str) -> bool:
    """发送飞书点对点消息（interactive card），返回是否成功"""
    token = feishu_get_token()
    if not token:
        return False
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    params = {"receive_id_type": "open_id"}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    card_content = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "red" if "告警" in title else "blue",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": message.replace("\n", "\n\n")}},
            {"tag": "hr"},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "AI 股票监控系统"}]},
        ],
    }
    payload = {
        "receive_id": FEISHU_USER_OPEN_ID,
        "msg_type": "interactive",
        "content": json.dumps(card_content),
    }
    try:
        resp = requests.post(url, params=params, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            logger.warning(f"飞书消息发送失败: {result}")
            return False
        logger.info(f"飞书通知已发送: [{title}] {message[:50]}")
        return True
    except Exception as e:
        logger.warning(f"飞书消息发送异常: {e}")
        return False
```

---

## Task 2: 在 `notify()` 末尾追加飞书发送

**文件:** `src/tools/stock_monitor.py` 第 904 行附近

找到 `notify()` 函数末尾：

```python
    except Exception as e:
        logger.error(f"发送通知失败: {e}")
```

在这段 `except` 块之后、下一行空行之前追加：

```python
    # 飞书通知（并行，不阻塞 macOS 通知）
    try:
        feishu_send(title, message)
    except Exception as e:
        logger.warning(f"飞书通知发送失败（不影响主流程）: {e}")
```

---

## Task 3: 手动测试

在项目根目录运行：

```bash
cd /Users/zhul1/Documents/aiWorkspace/ai-investor
uv run python -c "
from src.tools.stock_monitor import feishu_send
feishu_send('飞书测试', '这是一条测试消息\n如果有收到请确认工作正常')
print('done')
"
```

预期：飞书收到卡片消息，标题「飞书测试」，内容「这是一条测试消息」。

---

## Task 4: 提交

```bash
git add src/tools/stock_monitor.py
git commit -m "feat: add Feishu parallel notification channel

- add feishu_get_token() with 2h token cache
- add feishu_send() for open_id direct message via interactive card
- call feishu_send() after each notify() call (non-blocking)"
```
