# Leader Election for Monitor Scripts

## Problem

当前多台机器可以同时运行 Poller/Notifier/L2 Daemon，导致：
- 重复写入 OneDrive 上的 `sim_trading.db`
- alerts 重复、冲突
- 可能重复下单

## Solution

用 SQLite DB 做简单的 leader election，保证只有一台机器运行 Python 监控脚本。

## Architecture

### Single Source of Truth

- SQLite `sim_trading.db` 的 `leader_election` 表作为锁源
- OneDrive 同步保证多机器间共享

### Table Schema

```sql
CREATE TABLE leader_election (
  lock_name TEXT PRIMARY KEY,     -- e.g. 'monitor_lock'
  machine_id TEXT NOT NULL,       -- hostname + pid, e.g. 'MacBook-Pro-123'
  pid INTEGER NOT NULL,           -- process PID
  heartbeat INTEGER NOT NULL      -- unix timestamp (秒)
);
```

### Lock Lifecycle

```
抢锁 → 持有锁（正常运行）→ 退出（释放锁）
                ↓
         心跳超时（5分钟无更新）→ 其他机器可抢
```

## Implementation

### 1. Machine ID

```python
import socket
import os

def get_machine_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"
```

### 2. Lock Manager Class

```python
class MonitorLock:
    LOCK_NAME = "monitor_lock"
    HEARTBEAT_INTERVAL = 30  # seconds
    HEARTBEAT_TIMEOUT = 180  # seconds (3 minutes)
    DB_PATH = "src/data/sim_trading.db"

    def __init__(self):
        self.machine_id = get_machine_id()
        self.conn = sqlite3.connect(self.DB_PATH, timeout=5)
        self.is_leader = False

    def try_acquire(self) -> bool:
        """尝试获取锁。成功返回 True，失败返回 False。"""
        now = int(time.time())

        # Step 1: 清理所有超时锁（任何机器的都清）
        with self.conn:
            self.conn.execute(
                "DELETE FROM leader_election WHERE heartbeat < ?",
                (now - self.HEARTBEAT_TIMEOUT,)
            )

        # Step 2: 尝试抢锁（原子操作）
        try:
            with self.conn:
                self.conn.execute(
                    """INSERT OR IGNORE INTO leader_election 
                       (lock_name, machine_id, pid, heartbeat) 
                       VALUES (?, ?, ?, ?)""",
                    (self.LOCK_NAME, self.machine_id, os.getpid(), now)
                )
                cursor = self.conn.execute(
                    "SELECT changes()"
                )
                acquired = cursor.fetchone()[0] == 1
        except sqlite3.OperationalError:
            return False

        self.is_leader = acquired
        return acquired

    def refresh_heartbeat(self) -> bool:
        """更新自己的心跳。返回 False 表示锁已丢失。"""
        now = int(time.time())
        with self.conn:
            self.conn.execute(
                """UPDATE leader_election 
                   SET heartbeat = ? 
                   WHERE lock_name = ? AND machine_id = ?""",
                (now, self.LOCK_NAME, self.machine_id)
            )
            return self.conn.total_changes > 0

    def release(self):
        """释放锁（退出时调用）。"""
        with self.conn:
            self.conn.execute(
                "DELETE FROM leader_election WHERE machine_id = ?",
                (self.machine_id,)
            )

    def is_lock_holder(self) -> bool:
        """检查自己是否还是锁持有者。"""
        now = int(time.time())
        cursor = self.conn.execute(
            """SELECT heartbeat FROM leader_election 
               WHERE lock_name = ? AND machine_id = ?""",
            (self.LOCK_NAME, self.machine_id)
        )
        row = cursor.fetchone()
        if not row:
            return False
        return (now - row[0]) < self.HEARTBEAT_TIMEOUT
```

### 3. Startup Sequence

```python
def main():
    lock = MonitorLock()
    
    if not lock.try_acquire():
        print("另一台机器已在运行监控脚本，退出")
        sys.exit(1)
    
    print("成功获取锁，成为 leader")
    
    try:
        while True:
            time.sleep(lock.HEARTBEAT_INTERVAL)
            if not lock.refresh_heartbeat():
                print("锁已丢失，退出")
                break
    finally:
        lock.release()
```

### 4. Startup Script Integration

在 `start_ai_investor_full.sh` 的 `do_start()` 启动各进程前，先用 Python 做锁检查：

```bash
# 检查锁
LOCK_STATUS=$(uv run python -c "
from src.tools.monitor_lock import MonitorLock
lock = MonitorLock()
if lock.try_acquire():
    print('acquired')
else:
    print('locked')
")
```

### 5. Table Creation

首次运行前创建表：

```sql
CREATE TABLE IF NOT EXISTS leader_election (
  lock_name TEXT PRIMARY KEY,
  machine_id TEXT NOT NULL,
  pid INTEGER NOT NULL,
  heartbeat INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_heartbeat ON leader_election(heartbeat);
```

## Scenarios

### Scenario 1: A 先启动，B 后启动

```
T1: A 启动 → 抢锁成功（heartbeat=T1）
T2: B 启动 → 清理超时锁 → B 抢锁失败 → B 退出
```

### Scenario 2: A 挂了，B 启动

```
A 运行中（正常 heartbeat）
A 崩溃（不再更新 heartbeat）
5min 后：
  B 启动 → 清理 A 的过期锁 → B 抢锁成功 → 成为 leader
```

### Scenario 3: A 恢复，B 还在运行

```
A 先抢到锁
A 挂了，B 抢到锁（5min 后）
10min 后 A 恢复：
  A 检查 → 发现 B 持有有效锁 → A 退出/只读
```

### Scenario 4: 两台机器同时启动

```
T1: A 和 B 同时启动
T2: 两台都执行 DELETE 清理（都是超时锁，互不影响）
T3: 两台都执行 INSERT OR IGNORE
    - 谁先执行谁抢到（SQLite 原子操作）
    - 抢到的是 leader，另一个退出
```

## Safety Rules

1. **锁粒度**：所有 Python 监控脚本共用一把锁（monitor_lock）
2. **心跳间隔**：30 秒
3. **心跳超时**：3 分钟（3 分钟无心跳视为死亡）
4. **清理策略**：只有抢锁前清理，不在运行时主动抢别人有效的锁
5. **退出时释放**：Python 进程退出时必须删除自己的锁记录

## File Changes

- `src/tools/monitor_lock.py` — 新文件，LockManager 类
- `start_ai_investor_full.sh` — 启动前先检查锁
- `src/tools/market_data_poller.py` — 集成锁管理
- `src/tools/stock_notifier.py` — 集成锁管理
- `src/tools/l2_strategy_daemon.py` — 集成锁管理

## Alternative: 进程检测

除了 DB 锁，也可以用进程检测做辅助：

```bash
# 检查是否已有其他 poller 进程在运行
if pgrep -f "market_data_poller.py" | grep -v $$ > /dev/null; then
    echo "已有 poller 运行"
fi
```

但进程检测不可靠（可能被 kill -9，无法检测另一台机器），DB 锁更可靠。
