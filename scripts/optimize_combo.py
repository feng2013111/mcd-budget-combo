#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦当劳预算最优套餐推荐引擎（mcd-budget-combo）

三种模式：
  value  性价比优先 —— 在预算内最大化「满足感 / 每一元」
  taste  好吃优先   —— 在预算内最大化口味分（启发式，可用关键词覆盖）
  save   省钱优先   —— 在达到目标满足感的前提下，结合优惠券把实付压到最低

输入数据来自麦当劳中国官方 MCP（https://mcp.mcd.cn）：
  query-meals / available-coupons / query-my-coupons / query-store-coupons
价格单位：官方返回为「分」，本脚本内部统一用「分」，展示时换算为「元」。

重要口径说明（详见 references/optimization.md）：
  * 券后价为「估算值」。麦当劳 MCP 的券叠加规则不透明，最终实付必须用
    calculate-price 工具核价，本脚本的折扣计算只用于排序参考。
  * 口味分为内置启发式，并非真实测评数据；请用 --prefer / --exclude 覆盖。

用法：
  python3 optimize_combo.py --menu menu.json --coupons coupons.json \
      --budget 30 --mode value --top 3
  python3 optimize_combo.py --demo --mode taste --budget 40
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------
# 常量与启发式数据
# --------------------------------------------------------------------------

CATEGORY_MAP = {
    # 官方菜单分类千奇百怪，这里归一化成四类结构 + 甜品
    "主食": "staple", "汉堡": "staple", "堡": "staple", "burger": "staple",
    "小食": "snack", "炸鸡": "snack", "薯条": "snack", "snack": "snack",
    "饮料": "drink", "饮品": "drink", "drink": "drink",
    "甜品": "dessert", "派": "dessert", "冰淇淋": "dessert", "dessert": "dessert",
    "套餐": "combo", "combo": "combo", "meal": "combo",
}

CATEGORY_LABEL = {
    "staple": "主食", "snack": "小食", "drink": "饮料",
    "dessert": "甜品", "combo": "套餐", "other": "其他",
}

# 内置启发式口味分（0-10）：仅覆盖常见单品，未列出的按分类默认分。
# 说明：这是「大众向粗略偏好」，不是麦当劳官方数据，也不是真实测评。
BUILTIN_TASTE: Dict[str, float] = {
    "巨无霸": 8.0, "双层吉士堡": 7.5, "麦辣鸡腿堡": 8.5, "板烧鸡腿堡": 8.5,
    "麦香鱼": 7.0, "麦满分": 7.5, "脆汁鸡": 8.0, "麦乐鸡": 7.5,
    "薯条": 8.5, "洋葱圈": 7.5, "香芋派": 8.0, "菠萝派": 7.5,
    "圆筒冰淇淋": 7.5, "麦旋风": 8.0, "可乐": 7.0, "雪碧": 6.5,
    "橙汁": 6.5, "咖啡": 7.0, "珍珠奶茶": 7.0,
}

CATEGORY_TASTE_DEFAULT = {
    "staple": 7.0, "snack": 7.0, "drink": 6.0, "dessert": 7.0,
    "combo": 7.5, "other": 5.0,
}

# 性价比模式的「满足感」权重：按品类给一个基础值，薯条饮料这类
# 便宜大碗的给高一点，让「1 元买到多少满足」有区分度。
CATEGORY_VALUE_DEFAULT = {
    "staple": 10.0, "snack": 7.0, "drink": 5.0, "dessert": 6.0,
    "combo": 14.0, "other": 4.0,
}

# 一顿「像样套餐」的结构约束（软约束，用于 value/save 模式的组合生成）
MEAL_SHAPE = {
    "staple": (1, 2),     # 1-2 份主食
    "snack": (0, 2),
    "drink": (0, 2),
    "dessert": (0, 1),
    "combo": (0, 1),
}


@dataclass
class Item:
    code: str
    name: str
    price: int                      # 分
    category: str = "other"
    taste: float = 0.0
    value: float = 0.0
    keywords: List[str] = field(default_factory=list)

    @property
    def price_yuan(self) -> float:
        return self.price / 100.0


@dataclass
class Coupon:
    cid: str
    name: str
    kind: str                       # "threshold" 满减 | "product" 指定商品
    discount: int                   # 分
    min_spend: int = 0              # 分（满减门槛）
    product_codes: List[str] = field(default_factory=list)
    store_code: str = ""            # 空 = 不限门店


# --------------------------------------------------------------------------
# 数据装载与归一化
# --------------------------------------------------------------------------

def _first(d: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """宽容取值：MCP 返回字段名不完全固定，按候选键依次尝试。"""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _price_to_fen(v: Any) -> int:
    """菜单价格转「分」。

    口径依据（references/mcp_tools.md）：query-meals 的 currentPrice
    实际返回「元」的字符串（如 "25.5"）。故：字符串一律按元处理；
    数值按经验判断——≥1000 视为「分」（千元级菜单项不存在），
    <1000 视为「元」。若官方调整口径，改这里即可。
    """
    if v is None:
        return 0
    if isinstance(v, str):
        v = v.strip().replace("¥", "").replace("元", "")
        try:
            f = float(v)
        except ValueError:
            return 0
        return int(round(f * 100))
    f = float(v)
    return int(round(f)) if f >= 1000 else int(round(f * 100))


def _coupon_money_to_fen(v: Any) -> int:
    """优惠券金额/门槛转「分」。

    官方未文档化券金额口径，而参考实现与实测反馈均表明 MCP 的
    金额单位是「分」（如 discount=500 表示减 5 元）。取保守规则：
    数值 <100 视为「元」（没有 1 元以下的券），其余视为「分」；
    字符串按「元」处理。若发现异常折扣（如减价超过原价），
    best_coupon 里有 min(discount, raw_price) 兜底。
    """
    if v is None:
        return 0
    if isinstance(v, str):
        v = v.strip().replace("¥", "").replace("元", "")
        try:
            f = float(v)
        except ValueError:
            return 0
        return int(round(f * 100))
    f = float(v)
    return int(round(f * 100)) if f < 100 else int(round(f))


def _lookup_taste(name: str, category: str) -> float:
    """查内置口味分：先精确匹配，再用「内置名是商品名子串」匹配
    （兼容「可乐（中）」「薯条（大）」这类带规格后缀的官方命名），
    多个命中取键最长者；都没有则用分类默认分。"""
    if name in BUILTIN_TASTE:
        return BUILTIN_TASTE[name]
    best_key, best_len = None, 0
    for key in BUILTIN_TASTE:
        if key in name and len(key) > best_len:
            best_key, best_len = key, len(key)
    if best_key:
        return BUILTIN_TASTE[best_key]
    return CATEGORY_TASTE_DEFAULT.get(category, 5.0)


def normalize_menu(raw: Dict[str, Any],
                   prefer: Optional[List[str]] = None,
                   exclude: Optional[List[str]] = None) -> List[Item]:
    """把 query-meals 的返回归一化为 Item 列表。"""
    prefer = prefer or []
    exclude = exclude or []

    # 兼容多种外层结构：{"items":[...]} / {"meals":[...]} / [...]
    if isinstance(raw, list):
        items_raw = raw
    else:
        items_raw = _first(raw, "items", "meals", "products", "data", default=[]) or []

    items: List[Item] = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        code = str(_first(it, "code", "productCode", "id", default=""))
        name = str(_first(it, "name", "title", default="未命名"))
        price = _price_to_fen(_first(it, "currentPrice", "price", "salePrice", "amount"))
        if not code or price <= 0:
            continue  # 无价商品无法参与优化

        raw_cat = str(_first(it, "category", "categoryName", "type", default="其他"))
        category = CATEGORY_MAP.get(raw_cat, CATEGORY_MAP.get(raw_cat.lower(), "other"))

        # 排除关键词（如「不吃辣」→ exclude=["辣"]）
        if any(kw in name for kw in exclude):
            continue

        taste = _lookup_taste(name, category)
        for kw in prefer:                       # 用户偏好加权，封顶 10
            if kw in name:
                taste = min(10.0, taste + 1.5)

        value = CATEGORY_VALUE_DEFAULT.get(category, 4.0)
        for kw in prefer:                       # 偏好同时提升满足感与口味分
            if kw in name:
                value += 2.0
        items.append(Item(code=code, name=name, price=price,
                          category=category, taste=taste, value=value))
    return items


def normalize_coupons(raw: Dict[str, Any]) -> List[Coupon]:
    """把优惠券相关工具的返回归一化为 Coupon 列表。"""
    if isinstance(raw, list):
        rows = raw
    else:
        rows = _first(raw, "coupons", "items", "availableCoupons",
                      "myCoupons", "data", default=[]) or []

    coupons: List[Coupon] = []
    for c in rows:
        if not isinstance(c, dict):
            continue
        cid = str(_first(c, "couponId", "id", "couponCode", default=""))
        name = str(_first(c, "name", "title", default="未命名券"))
        # 金额字段在不同工具里叫法不同；口径见 _coupon_money_to_fen
        discount = _coupon_money_to_fen(_first(c, "discount", "amount", "value", "denomination"))

        scope = _first(c, "productCodes", "productList", "scopeProducts", default=[]) or []
        min_spend = _coupon_money_to_fen(_first(c, "minSpend", "threshold", "minAmount",
                                                "conditionAmount", default=0))
        kind = "product" if scope else "threshold"
        if kind == "threshold" and min_spend <= 0:
            # 满减券没有门槛字段时保守跳过：无法判断是否可用
            continue

        store = str(_first(c, "storeCode", "storeId", default="") or "")
        coupons.append(Coupon(cid=cid, name=name, kind=kind, discount=discount,
                              min_spend=min_spend,
                              product_codes=[str(x) for x in scope],
                              store_code=store))
    return coupons


# --------------------------------------------------------------------------
# 组合生成
# --------------------------------------------------------------------------

@dataclass
class Combo:
    items: List[Item]
    coupon: Optional[Coupon]

    @property
    def raw_price(self) -> int:
        return sum(i.price for i in self.items)

    @property
    def final_price(self) -> int:
        """券后估算价。只取「最优的一张券」，不假设可叠加。"""
        return max(0, self.raw_price - self.coupon.discount) if self.coupon else self.raw_price

    def value_sum(self) -> float:
        return sum(i.value for i in self.items)

    def taste_sum(self) -> float:
        return sum(i.taste for i in self.items)


def _rank_key_value(it: Item) -> float:
    return it.value / max(it.price, 1) * 100  # 每「分」的满足感


def _rank_key_taste(it: Item) -> float:
    return it.taste / max(it.price, 1) * 100


def _category_top(items: List[Item], cat: str, k: int, key) -> List[Item]:
    pool = [it for it in items if it.category == cat]
    pool.sort(key=key, reverse=True)
    return pool[:k]


def _enumerate_meals(items: List[Item], budget: int, key,
                     max_items: int, top_k: int = 8) -> List[Combo]:
    """
    按 MEAL_SHAPE 生成候选套餐：每个品类取性价比/口味前 top_k 个，
    再枚举品类内 0..上限 的组合，用预算剪枝。复杂度可控。
    """
    candidates: Dict[str, List[Item]] = {}
    for cat in ("staple", "snack", "drink", "dessert", "combo"):
        lo, hi = MEAL_SHAPE[cat]
        if hi == 0:
            continue
        ranked = _category_top(items, cat, top_k, key)
        # 品类内选 0..hi 个：单枚举（取前若干个）避免组合爆炸
        combos_in_cat: List[List[Item]] = [[]]
        pool = ranked[:hi]
        # 单元素 + 双元素（hi=2 时）逐级扩展
        for it in pool:
            combos_in_cat.append([it])
        if hi >= 2:
            for a in range(len(pool)):
                for b in range(a + 1, len(pool)):
                    combos_in_cat.append([pool[a], pool[b]])
        candidates[cat] = combos_in_cat

    # 跨品类笛卡尔积 + 预算剪枝 + 数量约束
    results: List[Combo] = []
    cats = list(candidates.keys())

    def recurse(idx: int, chosen: List[Item], spent: int) -> None:
        if idx == len(cats):
            if 1 <= len(chosen) <= max_items:
                results.append(Combo(items=list(chosen), coupon=None))
            return
        for pick in candidates[cats[idx]]:
            add = sum(i.price for i in pick)
            if spent + add > budget:
                continue
            if len(chosen) + len(pick) > max_items:
                continue
            recurse(idx + 1, chosen + pick, spent + add)

    recurse(0, [], 0)
    return results


def best_coupon(combo: Combo, coupons: List[Coupon],
                store_code: str = "") -> Optional[Coupon]:
    """给一个套餐挑最优的一张券（product 券按命中商品折扣一次计）。"""
    best: Optional[Coupon] = None
    best_discount = 0
    codes = {i.code for i in combo.items}
    for c in coupons:
        if c.store_code and store_code and c.store_code != store_code:
            continue  # 门店券不匹配当前门店
        discount = 0
        if c.kind == "threshold":
            if combo.raw_price >= c.min_spend:
                discount = min(c.discount, combo.raw_price)
        else:  # product
            if codes & set(c.product_codes):
                discount = c.discount  # 保守：按一张计一次
        if discount > best_discount:
            best, best_discount = c, discount
    return best


# --------------------------------------------------------------------------
# 模式实现
# --------------------------------------------------------------------------

def mode_value(combos: List[Combo], coupons: List[Coupon],
               store_code: str, top: int) -> List[Combo]:
    """性价比：满足感 / 券后实付（每元），越高越好。
    要求组合「像一顿餐」：至少 2 个单品，或含套餐类单品。"""
    scored = []
    for cb in combos:
        is_meal = len(cb.items) >= 2 or any(i.category == "combo" for i in cb.items)
        if not is_meal:
            continue
        cb.coupon = best_coupon(cb, coupons, store_code)
        fp_yuan = cb.final_price / 100.0
        if fp_yuan <= 0:
            continue
        scored.append((cb.value_sum() / fp_yuan, cb))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [cb for _, cb in scored[:top]]


def mode_taste(combos: List[Combo], coupons: List[Coupon],
               store_code: str, top: int) -> List[Combo]:
    """好吃：口味分最高，同分看实付更低。"""
    scored = []
    for cb in combos:
        cb.coupon = best_coupon(cb, coupons, store_code)
        scored.append((cb.taste_sum(), -cb.final_price, cb))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [cb for *_, cb in scored[:top]]


def mode_save(combos: List[Combo], coupons: List[Coupon],
              store_code: str, top: int, target_value: float = 12.0) -> List[Combo]:
    """
    省钱：满足感达到 target_value 的套餐中，实付最低者胜。
    target_value 默认 12 ≈「一个主食 + 一份小食」的基础满足感。
    """
    ok = []
    for cb in combos:
        cb.coupon = best_coupon(cb, coupons, store_code)
        if cb.value_sum() >= target_value:
            ok.append(cb)
    ok.sort(key=lambda cb: (cb.final_price, -cb.value_sum()))
    return ok[:top]


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------

def render(combos: List[Combo], mode: str, budget_yuan: float) -> str:
    lines = []
    mode_label = {"value": "性价比优先", "taste": "好吃优先", "save": "省钱优先"}[mode]
    lines.append(f"模式：{mode_label}    预算：¥{budget_yuan:.2f}")
    lines.append("-" * 56)
    if not combos:
        lines.append("没有找到满足条件的组合，请放宽预算或调整偏好。")
        return "\n".join(lines)

    for rank, cb in enumerate(combos, 1):
        raw_y, fin_y = cb.raw_price / 100, cb.final_price / 100
        lines.append(f"#{rank}  实付约 ¥{fin_y:.2f}"
                     f"（原价 ¥{raw_y:.2f}"
                     + (f"，券后省 ¥{(raw_y - fin_y):.2f}）" if cb.coupon else "）"))
        for it in cb.items:
            lines.append(f"    - {it.name}  ¥{it.price_yuan:.2f}"
                         f"（{CATEGORY_LABEL.get(it.category, it.category)}）")
        if cb.coupon:
            lines.append(f"    * 用券：{cb.coupon.name}（-¥{cb.coupon.discount / 100:.2f}）")
        ratio = cb.value_sum() / max(cb.final_price / 100.0, 0.01)
        lines.append(f"    满足感 {cb.value_sum():.1f} / 口味分 {cb.taste_sum():.1f}"
                     f" / 性价比 {ratio:.2f} 满足感每元")
        lines.append("")
    lines.append("注：券后价为估算，实付请以 MCP calculate-price 核价为准。")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 样例数据（无 MCP Token 时用于自测；价格仅作演示，非实时）
# --------------------------------------------------------------------------

SAMPLE_MENU = {
    "items": [
        {"code": "9900005456", "name": "巨无霸", "currentPrice": "25.5", "category": "主食"},
        {"code": "9900005457", "name": "麦辣鸡腿堡", "currentPrice": "23.0", "category": "主食"},
        {"code": "9900005458", "name": "板烧鸡腿堡", "currentPrice": "24.0", "category": "主食"},
        {"code": "9900005459", "name": "双层吉士堡", "currentPrice": "18.5", "category": "主食"},
        {"code": "9900005460", "name": "麦香鱼", "currentPrice": "20.0", "category": "主食"},
        {"code": "9900005461", "name": "1+1随心配（红区）", "currentPrice": "12.9", "category": "combo"},
        {"code": "9900005462", "name": "麦乐鸡（4块）", "currentPrice": "12.5", "category": "小食"},
        {"code": "9900005463", "name": "薯条（大）", "currentPrice": "14.0", "category": "小食"},
        {"code": "9900005464", "name": "洋葱圈", "currentPrice": "11.0", "category": "小食"},
        {"code": "9900005465", "name": "可乐（中）", "currentPrice": "10.0", "category": "饮料"},
        {"code": "9900005466", "name": "雪碧（中）", "currentPrice": "10.0", "category": "饮料"},
        {"code": "9900005467", "name": "香芋派", "currentPrice": "8.5", "category": "甜品"},
        {"code": "9900005468", "name": "圆筒冰淇淋", "currentPrice": "6.0", "category": "甜品"},
    ]
}

SAMPLE_COUPONS = {
    "coupons": [
        {"couponId": "C001", "name": "满30减5", "discount": 500, "minSpend": 3000, "kind": "threshold"},
        {"couponId": "C002", "name": "麦乐鸡专享券", "discount": 300, "productCodes": ["9900005462"]},
        {"couponId": "C003", "name": "薯条立减3元", "discount": 300, "productCodes": ["9900005463"]},
    ]
}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="麦当劳预算最优套餐推荐引擎")
    ap.add_argument("--menu", help="query-meals 返回的 JSON 文件")
    ap.add_argument("--coupons", help="优惠券 JSON 文件（可用逗号合并多个来源）")
    ap.add_argument("--budget", type=float, required=True, help="预算（元）")
    ap.add_argument("--mode", choices=["value", "taste", "save"], default="value")
    ap.add_argument("--top", type=int, default=3, help="输出前 N 个方案")
    ap.add_argument("--max-items", type=int, default=5, help="单套餐最多单品数")
    ap.add_argument("--prefer", default="", help="偏好关键词，逗号分隔（如：辣,鸡腿）")
    ap.add_argument("--exclude", default="", help="排除关键词，逗号分隔（如：鱼,辣）")
    ap.add_argument("--store", default="", help="当前门店 storeCode（用于筛门店券）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--demo", action="store_true", help="用内置样例数据演示")
    args = ap.parse_args(argv)

    budget_fen = int(round(args.budget * 100))
    prefer = [s.strip() for s in args.prefer.split(",") if s.strip()]
    exclude = [s.strip() for s in args.exclude.split(",") if s.strip()]

    if args.demo:
        menu_raw, coupons_raw = SAMPLE_MENU, SAMPLE_COUPONS
    else:
        if not args.menu:
            ap.error("需要 --menu 文件，或使用 --demo 演示")
        with open(args.menu, encoding="utf-8") as f:
            menu_raw = json.load(f)
        coupons_raw = {"coupons": []}
        if args.coupons:
            merged = []
            for path in args.coupons.split(","):
                with open(path.strip(), encoding="utf-8") as f:
                    part = json.load(f)
                if isinstance(part, list):
                    merged.extend(part)
                else:
                    merged.extend(_first(part, "coupons", "items", "data", default=[]) or [])
            coupons_raw = {"coupons": merged}

    items = normalize_menu(menu_raw, prefer=prefer, exclude=exclude)
    coupons = normalize_coupons(coupons_raw)
    if not items:
        print("菜单为空或全部被过滤，请检查数据。", file=sys.stderr)
        return 1

    combos = _enumerate_meals(items, budget_fen,
                              key=_rank_key_value if args.mode != "taste" else _rank_key_taste,
                              max_items=args.max_items)

    if args.mode == "value":
        top = mode_value(combos, coupons, args.store, args.top)
    elif args.mode == "taste":
        top = mode_taste(combos, coupons, args.store, args.top)
    else:
        top = mode_save(combos, coupons, args.store, args.top)

    if args.json:
        payload = [{
            "final_price_yuan": round(cb.final_price / 100, 2),
            "raw_price_yuan": round(cb.raw_price / 100, 2),
            "items": [{"name": i.name, "code": i.code, "price_yuan": i.price_yuan} for i in cb.items],
            "coupon": {"name": cb.coupon.name, "discount_yuan": cb.coupon.discount / 100} if cb.coupon else None,
            "value_score": round(cb.value_sum(), 1),
            "taste_score": round(cb.taste_sum(), 1),
        } for cb in top]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render(top, args.mode, args.budget))
    return 0


if __name__ == "__main__":
    sys.exit(main())
