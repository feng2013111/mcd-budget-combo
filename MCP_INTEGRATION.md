# MCP_INTEGRATION.md · 麦当劳 MCP 集成说明

> 项目名称：麦当劳预算点餐师（mcd-budget-combo）
> 本文说明项目实际使用的麦当劳 MCP Server、工具、调用流程与业务价值。

## 一、使用的 MCP Server

| 项 | 内容 |
| --- | --- |
| Server | 麦当劳中国官方 MCP（`https://mcp.mcd.cn`） |
| 协议 | streamable HTTP + JSON-RPC 2.0 |
| 鉴权 | `Authorization: Bearer <MCD_MCP_TOKEN>`，Token 经 https://open.mcd.cn/mcp 控制台申请 |
| 配置示例 | 见仓库根目录 `mcp-config.example.json`（仅环境变量占位符） |

## 二、实际使用的 Tool

| 工具 | 用途 | 调用时机 |
| --- | --- | --- |
| `query-meals` | 拉取门店菜单（商品 `code`、`name`、`currentPrice`） | 每次推荐前必调，作为优化的数据底座 |
| `available-coupons` | 麦麦省可领优惠券 | 每次推荐前拉取，与「我的券」「门店券」合并 |
| `query-my-coupons` | 已领取的优惠券 | 同上 |
| `query-store-coupons` | 当前门店可用券 | 同上 |
| `calculate-price` | 官方核价 | **推荐结果输出前必调**，作为唯一权威价格 |
| `query-nearby-stores` | 按定位查附近门店，取 `storeCode` | 用户指定「堂食/某家店」时 |

未使用但有认知的边界：`query-promotions` 实测仅企业团餐（`beType=6`）可用，个人用户无法借它做满减组合，因此本项目的优惠券策略为「单张最优券」的保守估算，绝不假设叠加。

## 三、调用流程

```
用户输入（预算/模式/偏好）
   │
   ├─1─> query-nearby-stores ──> storeCode
   │
   ├─2─> query-meals(storeCode) ──> menu.json ─┐
   │                                          │
   ├─3─> available-coupons ─┐                 │
   │   query-my-coupons ────┼─> coupons.json ─┤
   │   query-store-coupons ─┘                 │
   │                                          ▼
   │                              optimize_combo.py（本地枚举+评分+匹配券）
   │                                          │
   └─4─<────────── 推荐组合（含估算券后价）<────┘
         │
         └─5─> calculate-price(productCode 列表) ──> 官方核价
                     │
                     └─> 以官方核价修正后输出给用户
```

关键实现细节（坑位处理）：

1. **`code` → `productCode` 转换**：`query-meals` 返回 `code`，`calculate-price` 需要 `productCode`，直接换算会静默返回 `price: 0`。`scripts/mcp_call.py` 提供 `--as-productcode` 开关完成转换；
2. **金额口径**：`query-meals` 的 `currentPrice` 为「元」字符串，其余金额工具按「分」处理，归一化逻辑见 `scripts/optimize_combo.py` 的 `_price_to_fen` / `_coupon_money_to_fen`；
3. **SSE 响应解析**：MCP 为 streamable HTTP，响应可能为 SSE（`data:` 行），客户端已处理 `initialize` → `notifications/initialized` → `tools/call` 完整握手。

## 四、业务价值

把「预算 X 元，麦当劳怎么点最好」从经验问题变成**可计算问题**：

- **对个人用户**：三种模式（性价比/好吃/省钱）直接回答「怎么点」，并自动匹配可用的官方优惠券，输出前经 `calculate-price` 核价，不做无据估算；
- **对麦当劳 MCP 生态**：示范了个人开发者如何把点餐类工具编排成「决策辅助」型 Skill——官方提供的是原子能力（查菜单、查券、核价），本项目补上的是「约束优化」这一层，且全程遵守「不自动下单」的安全边界。
