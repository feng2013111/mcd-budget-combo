# 麦当劳中国官方 MCP 工具清单与实测坑位

来源：`https://mcp.mcd.cn`（麦当劳中国开放平台，open.mcd.cn/mcp 控制台取 Token）。
协议：streamable HTTP + JSON-RPC 2.0；鉴权头 `Authorization: Bearer <MCD_MCP_TOKEN>`。

> 以下工具清单与坑位来自 2026-10 的社区实测记录（腾讯云开发者社区 / 稀土掘金接入实录），
> 官方文档未全覆盖，以 `tools/list` 实时返回为准。

## 一、工具总览（35 个，按职能分组）

| 分组 | 工具 |
| --- | --- |
| 时间与门店 | `now-time-info`、`query-nearby-stores`、`delivery-query-stores` |
| 外送地址 | `delivery-query-addresses`、`delivery-create-address` |
| 菜单与详情 | `query-meals`、`query-meal-detail` |
| 营养数据 | `list-nutrition-foods` |
| 优惠券 | `available-coupons`、`auto-bind-coupons`、`query-my-coupons`、`query-store-coupons` |
| 算价 | `calculate-price` |
| 下单与订单 | `create-order`、`query-order`、`cancel-order`、`order-list` |
| 积分与商城 | `query-my-account`、`mall-points-products`、`mall-product-detail`、`mall-create-order` |
| 抽奖与活动 | `query-lottery-info`、`draw-lottery`、`campaign-calendar`、`query-my-prizes` |
| 派对预订 | `query-party-city`、`query-party-store`、`query-party-store-session`、`party-order-create` |
| 未文档化 | `query-promotions`、`query-survey-coupon` |

## 二、本 Skill 用到的工具及要点

### query-meals（菜单）

- 入参示例：`{"storeCode": "1950564"}`；
- 返回商品标识字段叫 **`code`**，价格为 `currentPrice`（**字符串，单位元**，如 `"25.5"`）；
- 返回示例：
  ```json
  { "code": "9900005456", "name": "巨无霸", "currentPrice": "25.5" }
  ```

### 优惠券三兄弟

| 工具 | 用途 |
| --- | --- |
| `available-coupons` | 麦麦省可领的券 |
| `query-my-coupons` | 已领取的「我的券」 |
| `query-store-coupons` | 当前门店可用券 |
| `auto-bind-coupons` | 一键领取（有副作用，先问用户） |

个人用户实际可用的券共 5 类：麦麦省可领 / 我的券 / 门店券 / 一键领 / CSAT 奖券。

### calculate-price（核价）

- 入参示例：
  ```json
  {
    "storeCode": "1950564",
    "orderType": 1,
    "beType": 1,
    "items": [{ "productCode": "9900005456", "quantity": 1 }]
  }
  ```
- 这是**唯一权威价格来源**。引擎算出的券后价只是排序参考，输出前必须用它复核。

## 三、实测坑位（重要）

1. **`code` vs `productCode` 静默陷阱**
   `query-meals` 返回 `code`，而 `calculate-price` 要 `productCode`。传错的后果不是报错，而是**直接返回 `price: 0`**——看起来像「免费」，极其危险。务必做字段名转换（`scripts/mcp_call.py --as-productcode` 已内置）。

2. **金额单位是「分」**
   社区参考实现与实测反馈一致：MCP 金额单位为分，展示时 `/100`。但 `query-meals` 的 `currentPrice` 实际是「元」的字符串——**两个口径不一致，务必逐字段确认**。本引擎的归一化策略见 `scripts/optimize_combo.py` 的 `_price_to_fen` / `_coupon_money_to_fen`。

3. **`query-promotions` 是死路（个人用户）**
   该工具 description 里写着仅企业团餐 `beType=6` 可用。任何依赖它做「满减券智能组合」的方案对个人用户无效。

4. **工具数对不上**
   官方文档写 33 个，`tools/list` 实际返回 35 个（多出 `query-promotions`、`query-survey-coupon`）。以实时 `tools/list` 为准。

5. **连接方式**
   streamable HTTP，握手需要 `initialize` → `notifications/initialized` → `tools/call`，响应可能是 SSE 格式（`data:` 行），需解析。`scripts/mcp_call.py` 已处理。

## 四、配置参考

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer ${MCD_MCP_TOKEN}" }
    }
  }
}
```
