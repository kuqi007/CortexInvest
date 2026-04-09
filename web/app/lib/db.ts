import { join } from "path";

// 直接使用 OneDrive 数据库（注意多设备冲突问题）
export const SIM_DB_PATH = join(
  process.cwd(),
  "..",
  "src",
  "data",
  "sim_trading.db"
);
