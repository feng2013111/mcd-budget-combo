#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦当劳中国官方 MCP 轻量调用器（仅依赖 Python 标准库）

MCP Server: https://mcp.mcd.cn（streamable HTTP，JSON-RPC 2.0）
鉴权：Authorization: Bearer <MCD_MCP_TOKEN>
Token 获取：https://open.mcd.cn/mcp 登录后进入「控制台」

用法：
  export MCD_MCP_TOKEN="你的token"
  python3 mcp_call.py now-time-info
  python3 mcp_call.py query-nearby-stores '{"latitude": 39.9, "longitude": 116.4}'
  python3 mcp_call.py query-meals '{"storeCode": "1950564"}'
  python3 mcp_call.py available-coupons

已知坑（见 references/mcp_tools.md）：
  1. query-meals 返回的商品标识叫 code，而 calculate-price 要的是
     productCode —— 直接换算会「不报错但返回 price: 0」。
     本脚本提供 --as-productcode 开关做字段名转换。
  2. 所有金额单位是「分」，展示时记得 /100。
  3. query-promotions 看似能查促销，实际仅企业团餐(beType=6)可用，
     个人用户不要依赖它做满减券组合。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

MCP_URL = "https://mcp.mcd.cn"


def _rpc(method: str, params: dict, token: str, session_id: str = "") -> dict:
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    req = urllib.request.Request(MCP_URL, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        session_id = resp.headers.get("Mcp-Session-Id", session_id)
        raw = resp.read().decode("utf-8")

    # streamable HTTP 可能返回 SSE 格式：取 data: 行
    if raw.lstrip().startswith("event:") or "\ndata:" in raw:
        for line in raw.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                break
    payload = json.loads(raw)
    payload["_session_id"] = session_id
    return payload


def call_tool(name: str, arguments: dict, token: str) -> dict:
    # 1) initialize（拿 session）
    init = _rpc("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "mcd-budget-combo", "version": "1.0.0"},
    }, token)
    sid = init.get("_session_id", "")
    _rpc("notifications/initialized", {}, token, sid)  # 2) 完成握手
    return _rpc("tools/call", {"name": name, "arguments": arguments}, token, sid)


def extract_items(result: dict) -> list:
    """从 tools/call 的 content 中提取结构化 JSON（若有）。"""
    content = result.get("result", {}).get("content", [])
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            try:
                data = json.loads(text)
                if isinstance(data, list):
                    return data
                return data.get("items") or data.get("coupons") or data.get("data") or []
            except (ValueError, AttributeError):
                continue
    return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="麦当劳 MCP 轻量调用器")
    ap.add_argument("tool", help="工具名，如 query-meals / available-coupons")
    ap.add_argument("args", nargs="?", default="{}", help="JSON 参数串")
    ap.add_argument("--as-productcode", action="store_true",
                    help="把返回里的 code 字段改名为 productCode（供 calculate-price 使用）")
    ap.add_argument("--pretty", action="store_true", default=True)
    ns = ap.parse_args(argv)

    token = os.environ.get("MCD_MCP_TOKEN", "")
    if not token:
        print("错误：请先设置环境变量 MCD_MCP_TOKEN\n"
              "Token 获取：https://open.mcd.cn/mcp 登录后进入「控制台」", file=sys.stderr)
        return 2

    try:
        arguments = json.loads(ns.args)
    except json.JSONDecodeError as e:
        print(f"参数不是合法 JSON：{e}", file=sys.stderr)
        return 2

    try:
        result = call_tool(ns.tool, arguments, token)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"网络错误：{e.reason}", file=sys.stderr)
        return 1

    if "error" in result:
        print(json.dumps(result["error"], ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    if ns.as_productcode:
        for it in extract_items(result):
            if isinstance(it, dict) and "code" in it and "productCode" not in it:
                it["productCode"] = it.pop("code")

    print(json.dumps(result.get("result", result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
