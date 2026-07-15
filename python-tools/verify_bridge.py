#!/usr/bin/env python3
"""
verify_bridge.py — 验证 Python 桥接脚本是否正常工作

测试内容：
1. agent_learning_bridge.py 的 memory 和 rag 方法
2. chemistry_server.py 的 MCP 协议握手 + 工具调用
3. physics_server.py 的 MCP 协议握手 + 工具调用

用法: python verify_bridge.py
"""

import json
import subprocess
import sys
import os
import time

# ── 配置 ──
PYTHON = sys.executable
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_LEARNING = r"D:\agent_learning"

PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
INFO = "\033[96mℹ\033[0m"


def send_jsonrpc(proc, method: str, params: dict = None, req_id: int = 1) -> dict:
    """发送 JSON-RPC 请求并读取响应。"""
    req = {"id": req_id, "method": method}
    if params:
        req["params"] = params
    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()

    # 读响应
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("No response from subprocess")
    return json.loads(line)


def send_mcp(proc, method: str, params: dict = None, req_id: int = 1) -> dict:
    """发送 MCP JSON-RPC 2.0 请求。"""
    req = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params:
        req["params"] = params
    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()

    # 可能有多行（notifications），读到有 id 的响应
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("No response from MCP server")
        msg = json.loads(line)
        if "id" in msg and msg["id"] == req_id:
            return msg


def test_bridge():
    """测试 agent_learning_bridge.py。"""
    print(f"\n{INFO} 测试 agent_learning_bridge.py...")

    proc = subprocess.Popen(
        [PYTHON, os.path.join(TOOLS_DIR, "agent_learning_bridge.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": AGENT_LEARNING},
    )

    try:
        # 测试 memory_stats
        print("  [1] memory_stats...", end=" ")
        resp = send_jsonrpc(proc, "memory_stats", {}, 1)
        if "result" in resp:
            print(f"{PASS} — episodic={resp['result'].get('episodic_count', '?')}")
        else:
            print(f"{FAIL} — {resp.get('error', 'unknown')}")

        # 测试 memory_search
        print("  [2] memory_search...", end=" ")
        resp = send_jsonrpc(proc, "memory_search", {"query": "化学", "limit": 3}, 2)
        if "result" in resp:
            print(f"{PASS} — found {len(resp['result'].get('items', []))} items")
        else:
            print(f"{FAIL} — {resp.get('error', 'unknown')}")

        # 测试 rag_stats
        print("  [3] rag_stats...", end=" ")
        resp = send_jsonrpc(proc, "rag_stats", {}, 3)
        if "result" in resp:
            print(f"{PASS} — count={resp['result'].get('count', '?')}")
        else:
            print(f"{FAIL} — {resp.get('error', 'unknown')}")

        # 测试 rag_search
        print("  [4] rag_search...", end=" ")
        resp = send_jsonrpc(proc, "rag_search", {"query": "热力学", "top_k": 3}, 4)
        if "result" in resp:
            print(f"{PASS} — found {len(resp['result'].get('results', []))} results")
        else:
            print(f"{FAIL} — {resp.get('error', 'unknown')}")

    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_mcp_server(script_name: str, test_tool: str, test_args: dict):
    """测试 MCP server 的完整握手 + 工具调用。"""
    print(f"\n{INFO} 测试 {script_name} (MCP 协议)...")

    proc = subprocess.Popen(
        [PYTHON, os.path.join(TOOLS_DIR, script_name)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": AGENT_LEARNING},
    )

    try:
        # Step 1: initialize
        print("  [1] initialize...", end=" ")
        resp = send_mcp(proc, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "verify_bridge", "version": "1.0.0"},
        }, 1)
        if "result" in resp:
            server_info = resp["result"].get("serverInfo", {})
            print(f"{PASS} — {server_info.get('name', '?')} v{server_info.get('version', '?')}")
        else:
            print(f"{FAIL} — {resp.get('error', 'no result')}")
            return

        # Step 2: notifications/initialized
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        time.sleep(0.3)

        # Step 3: tools/list
        print("  [2] tools/list...", end=" ")
        resp = send_mcp(proc, "tools/list", {}, 2)
        if "result" in resp:
            tools = resp["result"].get("tools", [])
            print(f"{PASS} — {len(tools)} tools available")
            for t in tools[:3]:
                print(f"       - {t['name']}: {t.get('description', '')[:60]}")
            if len(tools) > 3:
                print(f"       ... and {len(tools) - 3} more")
        else:
            print(f"{FAIL} — {resp.get('error', 'no result')}")
            return

        # Step 4: tools/call (test one tool)
        print(f"  [3] tools/call ({test_tool})...", end=" ")
        resp = send_mcp(proc, "tools/call", {
            "name": test_tool,
            "arguments": test_args,
        }, 3)
        if "result" in resp:
            content = resp["result"].get("content", [])
            if content:
                text = content[0].get("text", "")
                parsed = json.loads(text) if text.startswith("{") or text.startswith("[") else text
                if isinstance(parsed, dict):
                    print(f"{PASS} — keys: {list(parsed.keys())[:5]}")
                elif isinstance(parsed, list):
                    print(f"{PASS} — {len(parsed)} items")
                else:
                    print(f"{PASS} — {str(parsed)[:60]}")
            else:
                print(f"{PASS} — empty content")
        else:
            error_msg = resp.get("error", {}).get("message", "unknown")
            print(f"{FAIL} — {error_msg}")

    finally:
        proc.terminate()
        proc.wait(timeout=5)


def main():
    print("=" * 50)
    print("  Agent Workflow — 桥接验证")
    print("=" * 50)

    # 检查 agent_learning
    if not os.path.isdir(AGENT_LEARNING):
        print(f"\n{FAIL} agent_learning 未找到: {AGENT_LEARNING}")
        sys.exit(1)

    # 测试 bridge
    try:
        test_bridge()
    except Exception as e:
        print(f"\n{FAIL} Bridge 测试异常: {e}")

    # 测试 chemistry MCP
    try:
        test_mcp_server(
            "chemistry_server.py",
            "balance_equation",
            {"equation": "H2 + O2 -> H2O"},
        )
    except Exception as e:
        print(f"\n{FAIL} Chemistry MCP 测试异常: {e}")

    # 测试 physics MCP
    try:
        test_mcp_server(
            "physics_server.py",
            "mechanics",
            {"problem": "A ball is thrown upward at 20 m/s. How high does it go?", "g": 9.8},
        )
    except Exception as e:
        print(f"\n{FAIL} Physics MCP 测试异常: {e}")

    print("\n" + "=" * 50)
    print("  验证完成")
    print("=" * 50)


if __name__ == "__main__":
    main()
