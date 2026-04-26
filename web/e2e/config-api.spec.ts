import { test, expect } from "@playwright/test";

const TEST_CODE_1 = `E2E${Date.now().toString(36).toUpperCase().slice(-6)}001`;
const TEST_CODE_2 = `E2E${Date.now().toString(36).toUpperCase().slice(-6)}002`;
const TEST_CODE_3 = `E2E${Date.now().toString(36).toUpperCase().slice(-6)}003`;

test.describe("Config API", () => {
  test("GET /api/config 返回配置", async ({ request }) => {
    const res = await request.get("/api/config");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("watchlist");
    expect(data).toHaveProperty("settings");
    expect(data).toHaveProperty("alerts");
    expect(typeof data.watchlist).toBe("object");
    expect(typeof data.settings).toBe("object");
    expect(typeof data.alerts).toBe("object");
  });

  test("POST add添加watching股票", async ({ request }) => {
    const addRes = await request.post("/api/config", {
      data: { action: "add", code: TEST_CODE_1, data: { type: "watching" } },
    });
    expect(addRes.ok()).toBeTruthy();
    const addData = await addRes.json();
    expect(addData.success).toBe(true);

    // 验证存在
    const getRes = await request.get("/api/config");
    const getData = await getRes.json();
    expect(getData.watchlist).toHaveProperty(TEST_CODE_1);

    // 清理
    await request.post("/api/config", {
      data: { action: "remove", code: TEST_CODE_1 },
    });
  });

  test("POST update修改股票", async ({ request }) => {
    // 先添加
    await request.post("/api/config", {
      data: { action: "add", code: TEST_CODE_2, data: { type: "holding", cost: 10.0, shares: 100 } },
    });

    // 更新
    const updateRes = await request.post("/api/config", {
      data: { action: "update", code: TEST_CODE_2, data: { cost: 15.5, shares: 200 } },
    });
    expect(updateRes.ok()).toBeTruthy();
    const updateData = await updateRes.json();
    expect(updateData.success).toBe(true);

    // 验证
    const getRes = await request.get("/api/config");
    const getData = await getRes.json();
    expect(getData.watchlist[TEST_CODE_2].cost).toBe(15.5);
    expect(getData.watchlist[TEST_CODE_2].shares).toBe(200);

    // 清理
    await request.post("/api/config", {
      data: { action: "remove", code: TEST_CODE_2 },
    });
  });

  test("POST remove删除股票", async ({ request }) => {
    // 先添加
    await request.post("/api/config", {
      data: { action: "add", code: TEST_CODE_3, data: { type: "watching" } },
    });

    // 删除
    const removeRes = await request.post("/api/config", {
      data: { action: "remove", code: TEST_CODE_3 },
    });
    expect(removeRes.ok()).toBeTruthy();
    const removeData = await removeRes.json();
    expect(removeData.success).toBe(true);

    // 验证不存在
    const getRes = await request.get("/api/config");
    const getData = await getRes.json();
    expect(getData.watchlist).not.toHaveProperty(TEST_CODE_3);
  });

  test("POST settings更新设置", async ({ request }) => {
    const res = await request.post("/api/config", {
      data: { action: "settings", settings: { poll_interval: 45 } },
    });
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.success).toBe(true);

    // 验证
    const getRes = await request.get("/api/config");
    const getData = await getRes.json();
    expect(getData.settings.poll_interval).toBe(45);

    // 恢复默认值
    await request.post("/api/config", {
      data: { action: "settings", settings: { poll_interval: 30 } },
    });
  });

  test("POST pin置顶股票", async ({ request }) => {
    const pinCode = `PIN${Date.now().toString(36).toUpperCase().slice(-6)}`;
    // 先添加
    await request.post("/api/config", {
      data: { action: "add", code: pinCode, data: { type: "watching" } },
    });

    // pin
    const pinRes = await request.post("/api/config", {
      data: { action: "pin", code: pinCode, value: true },
    });
    expect(pinRes.ok()).toBeTruthy();
    const pinData = await pinRes.json();
    expect(pinData.success).toBe(true);

    // 验证 pin_order 变化
    const getRes = await request.get("/api/config");
    const getData = await getRes.json();
    expect(getData.watchlist[pinCode].pin_order).toBeGreaterThan(0);

    // unpin
    await request.post("/api/config", {
      data: { action: "pin", code: pinCode, value: false },
    });

    // 清理
    await request.post("/api/config", {
      data: { action: "remove", code: pinCode },
    });
  });

  test("POST tag-add/tag-remove", async ({ request }) => {
    const tagCode = `TAG${Date.now().toString(36).toUpperCase().slice(-6)}`;
    // 先添加
    await request.post("/api/config", {
      data: { action: "add", code: tagCode, data: { type: "watching" } },
    });

    // tag-add
    const addRes = await request.post("/api/config", {
      data: { action: "tag-add", codes: [tagCode], tag: "e2e-test" },
    });
    expect(addRes.ok()).toBeTruthy();
    const addData = await addRes.json();
    expect(addData.success).toBe(true);

    // 验证 tag 存在
    const getRes1 = await request.get("/api/config");
    const getData1 = await getRes1.json();
    expect(getData1.watchlist[tagCode].tags).toContain("e2e-test");

    // tag-remove
    const rmRes = await request.post("/api/config", {
      data: { action: "tag-remove", codes: [tagCode], tag: "e2e-test" },
    });
    expect(rmRes.ok()).toBeTruthy();
    const rmData = await rmRes.json();
    expect(rmData.success).toBe(true);

    // 验证 tag 已移除
    const getRes2 = await request.get("/api/config");
    const getData2 = await getRes2.json();
    const tags = getData2.watchlist[tagCode].tags || [];
    expect(tags).not.toContain("e2e-test");

    // 清理
    await request.post("/api/config", {
      data: { action: "remove", code: tagCode },
    });
  });

  test("错误处理 - missing code返回400", async ({ request }) => {
    const res = await request.post("/api/config", {
      data: { action: "add", data: { type: "watching" } },
    });
    expect(res.status()).toBe(400);
    const data = await res.json();
    expect(data.success).toBe(false);
  });
});
