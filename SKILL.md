---
name: mcd-budget-combo
description: 麦当劳预算最优套餐推荐。基于麦当劳中国官方 MCP（mcp.mcd.cn）的菜单、优惠券、门店数据，在给定预算内计算「怎么点吃得最好」——支持性价比优先、好吃优先、省钱优先（自动匹配优惠券）三种模式。当用户询问麦当劳点餐建议、套餐搭配、预算内吃什么、怎么点麦当劳最划算、麦当劳优惠券怎么用时报触发本技能。
agent_created: true
---

# 麦当劳预算点餐师（mcd-budget-combo）

## 概述

把「预算 X 元，麦当劳怎么点最好」变成可计算的问题：通过麦当劳中国官方 MCP 拉取实时菜单与优惠券，用本地优化引擎（`scripts/optimize_combo.py`）枚举合规套餐组合，按用户选择的模式排序输出，并给出优惠券使用建议。

三种模式：

| 模式 | 目标 | 适用提问 |
| --- | --- | --- |
| `value` 性价比 | 最大化「满足感 / 每元」 | 「30 块怎么吃最饱最值」 |
| `taste` 好吃 | 预算内口味分最高 | 「40 块想吃得开心」 |
| `save` 省钱 | 达标前提下实付最低 | 「20 块随便吃点，怎么最便宜」 |

## 前置条件

1. 麦当劳 MCP 已配置（Token 从 https://open.mcd.cn/mcp 控制台获取）：

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

2. 确认用户已提供：预算（元）、模式（未指定则先问，默认 `value`）、偏好/忌口关键词（可选）、用餐方式（堂食/外送，影响门店查询工具的选择）。

## 工作流

### 第 1 步：定位门店与菜单

- 堂食：调用 `query-nearby-stores`（需用户位置），从返回中取 `storeCode`；
- 外送：调用 `delivery-query-stores` 或让用户从 `delivery-query-addresses` 中选择地址；
- 调 `query-meals`（传 storeCode）拿菜单，保存为 `menu.json`。

若用户只要「大概怎么点」不要精确门店，可跳过门店步骤，仅用 `query-meals` 的默认菜单并注明「以实际门店为准」。

### 第 2 步：拉取优惠券

依次调用并合并以下来源（用逗号分隔喂给优化引擎的 `--coupons`）：

- `available-coupons`（麦麦省可领）
- `query-my-coupons`（我的券）
- `query-store-coupons`（当前门店券）

个人用户**不要**依赖 `query-promotions` 做满减组合——该工具实测仅企业团餐（beType=6）可用，详见 references/mcp_tools.md。

### 第 3 步：运行优化引擎

```bash
python3 scripts/optimize_combo.py \
  --menu menu.json --coupons coupons1.json,coupons2.json \
  --budget 30 --mode value --top 3 \
  --store <storeCode> [--prefer 鸡腿,辣] [--exclude 鱼] [--json]
```

- `--json` 输出机器可读结果，便于二次加工；
- 引擎只做本地枚举与估算，**不触网、不下单**。

### 第 4 步：核价（必须）

对推荐的首选组合，调 `calculate-price` 核验实付（注意：`query-meals` 返回的字段是 `code`，`calculate-price` 需要的是 `productCode`，直接换算的坑见 references/mcp_tools.md）。若引擎估算与官方核价不一致，**以官方核价为准**并在回复中修正数字。

### 第 5 步：输出给用户

包含：推荐组合（单品+价格）、原价/券后价、用券建议、性价比或口味分指标、以及「实付以 calculate-price 为准」的提示。忌口与偏好必须体现在结果中（如排除鱼类则不得出现麦香鱼）。

## 安全约束（硬性）

1. **绝不自动下单。** `create-order` 涉及真实扣款，仅在用户明确确认「就点这个」后才可调用，且调用前必须复述订单内容与金额。
2. 不虚构菜单、价格、优惠券。MCP 查不到的信息标注「未知」，不用训练数据里的旧价格填空。
3. 优惠券叠加规则不透明，引擎只按「单张最优券」估算，不得向用户承诺可叠加。

## 资源

- `scripts/optimize_combo.py` — 优化引擎（纯标准库，`--demo` 可用内置样例数据离线演示）
- `scripts/mcp_call.py` — MCP 轻量调用器（JSON-RPC over streamable HTTP，支持 `--as-productcode` 字段转换）
- `references/mcp_tools.md` — 35 个工具的清单、参数与实测坑位
- `references/optimization.md` — 评分模型、组合枚举算法与优惠券匹配逻辑
- `assets/sample_data.json` — 离线演示数据（价格非实时）

## 快速自测（无 Token）

```bash
python3 scripts/optimize_combo.py --demo --budget 30 --mode value --top 3
```
