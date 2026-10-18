# 麦当劳预算点餐师（mcd-budget-combo）

> 把「预算 X 元，麦当劳怎么点最好」变成可计算的问题。
> 参加 **1024 程序员节 · 麦当劳程序员创意开发大赛**（2026-10-09 ~ 10-25）作品，全程使用 WorkBuddy 开发（开发记录见 [workbuddy.md](workbuddy.md)）。

一个基于**麦当劳中国官方 MCP**（mcp.mcd.cn）的 WorkBuddy Skill：拉取实时菜单与优惠券，用本地优化引擎在预算内枚举套餐组合，按三种模式给出「怎么点最好」的答案，并用官方 `calculate-price` 核价。

## 目标用户

- **想吃麦当劳又怕超支的人**：报个预算，30 秒得到「怎么点最值」的完整方案；
- **选择困难症**：不说「随便」，让三种模式（性价比/好吃/省钱）替你决策；
- **想薅尽每一张券的人**：自动匹配麦麦省、我的券、门店券，输出前经官方核价；
- **WorkBuddy / MCP 生态开发者**：一个「原子工具 → 决策型 Skill」的完整编排样例（含 5 个实测坑位处理）。

## 三种模式

| 模式 | 目标 | 示例提问 |
| --- | --- | --- |
| `value` 性价比 | 满足感 / 每元 最高 | 「30 块怎么吃最值」 |
| `taste` 好吃 | 预算内口味分最高 | 「40 块想吃得开心」 |
| `save` 省钱 | 达标前提下实付最低，自动用券 | 「20 块随便吃点，怎么最便宜」 |

## 效果演示（内置样例数据）

```bash
python3 scripts/optimize_combo.py --demo --budget 40 --mode taste --top 2
```

```
模式：好吃优先    预算：¥40.00
--------------------------------------------------------
#1  实付约 ¥34.90（原价 ¥39.90，券后省 ¥5.00）
    - 洋葱圈  ¥11.00（小食）
    - 可乐（中）  ¥10.00（饮料）
    - 圆筒冰淇淋  ¥6.00（甜品）
    - 1+1随心配（红区）  ¥12.90（套餐）
    * 用券：满30减5（-¥5.00）
    满足感 32.0 / 口味分 29.5 / 性价比 0.92 满足感每元

#2  实付约 ¥35.00（原价 ¥40.00，券后省 ¥5.00）
    - 薯条（大）  ¥14.00（小食）
    - 可乐（中）  ¥10.00（饮料）
    - 雪碧（中）  ¥10.00（饮料）
    - 圆筒冰淇淋  ¥6.00（甜品）
    * 用券：满30减5（-¥5.00）
    满足感 23.0 / 口味分 29.5 / 性价比 0.58 满足感每元
```

（以上为 `--demo` 内置样例数据的真实输出；样例价格非实时菜单。）

## 安装与配置

### 1. 获取 MCP Token

登录 https://open.mcd.cn/mcp ，进入「控制台」激活领取。

### 2. 配置 MCP（WorkBuddy）

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

### 3. 安装 Skill

将本目录放入 `~/.workbuddy/skills/mcd-budget-combo/`（用户级）或项目的 `.workbuddy/skills/`（项目级）。

之后在 WorkBuddy 里直接说「40 块钱吃麦当劳怎么点最好」即可触发。

## 作为命令行工具独立使用

不装 Skill 也能用，纯 Python 标准库，无第三方依赖：

```bash
# 拉数据（需要 MCP Token）
export MCD_MCP_TOKEN="你的token"
python3 scripts/mcp_call.py query-meals '{"storeCode": "1950564"}' > menu.json
python3 scripts/mcp_call.py available-coupons > coupons.json

# 优化
python3 scripts/optimize_combo.py --menu menu.json --coupons coupons.json \
    --budget 30 --mode value --top 3 --prefer 鸡腿 --exclude 鱼

# 无 Token 演示
python3 scripts/optimize_combo.py --demo --budget 30 --mode value --top 3
```

## 它是怎么工作的

1. **取数**：`query-meals`（菜单）+ `available-coupons` / `query-my-coupons` / `query-store-coupons`（优惠券）；
2. **归一化**：金额统一转「分」，分类归一为 主食/小食/饮料/甜品/套餐（`references/mcp_tools.md` 记录了 5 个实测坑位，包括 `code`→`productCode` 静默返回 `price:0` 的陷阱）；
3. **枚举**：按「一顿像样的餐」结构约束（主食 1-2 件等）生成候选组合，预算剪枝；
4. **评分排序**：三种模式各自的目标函数（`references/optimization.md` 有完整模型与局限说明）；
5. **核价**：推荐结果必须经官方 `calculate-price` 复核——**引擎的券后价只是估算，不承诺可叠加**。

## 安全设计

- **绝不自动下单**：`create-order` 涉及真实扣款，只在用户明确确认后调用；
- 不虚构菜单/价格/券，查不到就标「未知」；
- 优惠券按「单张最优」保守估算，不假设叠加。

## 目录结构

```
├── README.md                    # 项目介绍、安装、示例、目标用户（大赛规定文件）
├── CONTEST_DECLARATION.md       # 参赛声明（官方原文，内容不可改动）
├── MCP_INTEGRATION.md           # MCP Server/Tool/调用流程/业务价值（大赛规定文件）
├── mcp-config.example.json      # 脱敏 MCP 配置示例（仅环境变量占位符）
├── workbuddy.md                 # WorkBuddy 开发记录（WorkBuddy 专项奖励须提交）
├── SKILL.md                     # Skill 定义（触发条件与工作流）
├── scripts/
│   ├── optimize_combo.py        # 优化引擎（三种模式，纯标准库）
│   └── mcp_call.py              # MCP 轻量调用器（JSON-RPC/SSE）
├── references/
│   ├── mcp_tools.md             # 35 个工具清单 + 实测坑位
│   └── optimization.md          # 评分模型与算法说明
├── assets/
│   └── sample_data.json         # 离线演示数据
└── LICENSE                      # MIT
```

## 大赛信息

- 赛事：1024 程序员节 · 麦当劳程序员创意开发大赛（报名截止 2026-10-25 23:59）
- 排名依据：GitHub 公开 Star 数（前 100 名进榜；Star 为 0 不进榜）
- 奖励：前 3 名分获巨无霸 50/30/20 次兑换券 + WorkBuddy 10240 积分；进榜即得程序员节徽章 + 汉堡回车键；使用 WorkBuddy 开发并提交 `workbuddy.md` 再得 3000 积分

## 致谢

麦当劳中国开放平台提供的 MCP 服务；坑位信息来自腾讯云开发者社区与稀土掘金社区的公开接入实录。

## License

MIT
