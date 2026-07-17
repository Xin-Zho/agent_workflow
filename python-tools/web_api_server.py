#!/usr/bin/env python3
"""
agent-workflow Web API Server

通过 Pi Agent RPC 模式（JSONL over stdin/stdout）提供 HTTP + WebSocket API。
架构：FastAPI → PiRpcBridge → pi --mode rpc (Node.js subprocess)

启动方式（在 WSL 中运行）:
    cd /mnt/d/agent_workflow/python-tools && python3 web_api_server.py --port 8000

环境变量：
    PI_CLI_PATH    Pi CLI 路径（默认自动查找）
    JWT_SECRET     JWT 签名密钥（生产环境必须设置）
    DEV_API_KEY    本地开发 API Key（默认 sk-dev，不应暴露到公网）
    WORKFLOW_DB_PATH 研究工作流 SQLite 数据库路径
"""

import asyncio
import json
import os
import sys
import uuid
import time
import signal
import hashlib
import hmac
from typing import Optional, AsyncIterator
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Header, Query
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn


# ── 配置 ────────────────────────────────────────────────────────────

PI_CLI_PATH = os.environ.get("PI_CLI_PATH")
if not PI_CLI_PATH:
    import shutil
    # 优先用 PATH 中的 pi 命令
    pi_in_path = shutil.which("pi")
    if pi_in_path:
        PI_CLI_PATH = pi_in_path
    else:
        # 回退到已知安装路径
        candidates = [
            os.path.expanduser("~/.local/share/pi-node/node-v22.23.1-linux-x64/bin/pi"),
            "/usr/local/bin/pi",
        ]
        for c in candidates:
            if os.path.exists(c):
                PI_CLI_PATH = c
                break

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-in-production")
DEV_API_KEY = os.environ.get("DEV_API_KEY", "sk-dev")
DEFAULT_PORT = int(os.environ.get("PORT", "8000"))


# ── JWT 工具 ────────────────────────────────────────────────────────

def create_token(user_id: str, expires_hours: int = 24, role: str = "user") -> str:
    """简易 JWT（HMAC-SHA256），生产环境换 PyJWT。"""
    header = base64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}))
    now = int(time.time())
    payload = base64url_encode(json.dumps({
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + expires_hours * 3600,
        "jti": uuid.uuid4().hex[:12],
    }))
    signature = base64url_encode(
        hmac.new(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    )
    return f"{header}.{payload}.{signature}"


def verify_token(token: str) -> Optional[dict]:
    """验证 JWT。返回 payload 或 None。"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, sig_b64 = parts
        expected_sig = base64url_encode(
            hmac.new(JWT_SECRET.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(sig_b64, expected_sig):
            return None
        payload = json.loads(base64url_decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def base64url_encode(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def base64url_decode(data: str) -> bytes:
    import base64
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)


# ── Pi RPC Bridge ───────────────────────────────────────────────────

class PiRpcBridge:
    """
    管理 Pi Agent RPC 子进程的 JSONL 通信。

    协议：stdin ← JSON 命令，stdout → JSON 事件/响应
    """

    def __init__(self, cli_path: str, cwd: str | None = None):
        self.cli_path = cli_path
        self.cwd = cwd or os.getcwd()
        self.proc: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """启动 Pi RPC 子进程。"""
        if self.proc is not None:
            return

        cmd = ["node", self.cli_path, "--mode", "rpc"]
        if not os.path.exists(self.cli_path):
            # 尝试通过 npx 启动
            cmd = ["npx", "@earendil-works/pi-coding-agent", "--mode", "rpc"]

        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env={**os.environ, "PI_CODING_AGENT": "true"},
        )

        self._reader_task = asyncio.create_task(self._read_loop())

    async def stop(self) -> None:
        """停止 Pi RPC 子进程。"""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self.proc:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except (asyncio.TimeoutError, Exception):
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

        # 清理待处理请求
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("Pi RPC process stopped"))
        self._pending.clear()

    async def _read_loop(self) -> None:
        """持续读取 stdout 的 JSONL 行，分发到 pending 请求或事件队列。"""
        try:
            while self.proc and self.proc.stdout:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                line_str = line.decode().strip()
                if not line_str:
                    continue

                try:
                    msg = json.loads(line_str)
                except json.JSONDecodeError:
                    continue

                msg_id = msg.get("id")
                if isinstance(msg_id, (int, str)) and msg.get("type") == "response":
                    # RPC 响应 → 匹配 pending 请求
                    fut = self._pending.pop(int(msg_id) if isinstance(msg_id, int) else hash(str(msg_id)), None)
                    if fut and not fut.done():
                        if msg.get("success"):
                            fut.set_result(msg.get("data", msg))
                        else:
                            fut.set_exception(RuntimeError(msg.get("error", "Unknown RPC error")))
                else:
                    # 事件 → 放入队列供 SSE/WS 消费
                    await self._event_queue.put(msg)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _send_cmd(self, cmd_type: str, **params) -> dict:
        """发送 RPC 命令并等待响应。"""
        async with self._lock:
            self._request_id += 1
            req_id = self._request_id
            cmd = {"id": req_id, "type": cmd_type, **params}
            self.proc.stdin.write((json.dumps(cmd) + "\n").encode())
            await self.proc.stdin.drain()

            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            self._pending[req_id] = fut
            try:
                return await asyncio.wait_for(fut, timeout=120)
            except asyncio.TimeoutError:
                self._pending.pop(req_id, None)
                raise HTTPException(status_code=504, detail=f"Pi RPC timeout: {cmd_type}")

    async def send_cmd_no_wait(self, cmd_type: str, **params) -> None:
        """发送 RPC 命令，不等待响应（用于 prompt/steer 等流式命令）。"""
        async with self._lock:
            self._request_id += 1
            req_id = self._request_id
            cmd = {"id": req_id, "type": cmd_type, **params}
            self.proc.stdin.write((json.dumps(cmd) + "\n").encode())
            await self.proc.stdin.drain()

    async def prompt(self, message: str) -> str:
        """发送 prompt 命令（阻塞，等待 agent_settled）。"""
        await self.send_cmd_no_wait("prompt", message=message)
        # 等待 agent_settled 事件
        while True:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=300)
                if event.get("type") == "agent_settled":
                    return "done"
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="Agent did not settle within timeout")

    async def get_state(self) -> dict:
        """获取当前状态。"""
        return await self._send_cmd("get_state")

    async def new_session(self, parent_session: str | None = None) -> dict:
        """创建新会话。"""
        params = {}
        if parent_session:
            params["parentSession"] = parent_session
        return await self._send_cmd("new_session", **params)

    def event_stream(self) -> "asyncio.Queue":
        """获取事件流队列（供 SSE/WS 消费）。"""
        return self._event_queue


# ── FastAPI 应用 ────────────────────────────────────────────────────

app = FastAPI(
    title="Agent Workflow API",
    description="Material Science Research Workflow + Pi Agent API",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局 Pi Bridge 实例
bridge: PiRpcBridge | None = None


@app.on_event("startup")
async def startup():
    global bridge
    if not PI_CLI_PATH:
        print("[startup] Pi CLI not found; research workflow API remains available")
        return
    bridge = PiRpcBridge(PI_CLI_PATH)
    try:
        await bridge.start()
        print(f"[startup] Pi RPC bridge started (CLI: {PI_CLI_PATH})")
    except Exception as exc:
        bridge = None
        print(f"[startup] Pi RPC unavailable; workflow-only mode: {exc}")


@app.on_event("shutdown")
async def shutdown():
    global bridge
    if bridge:
        await bridge.stop()
        bridge = None
    print("[shutdown] Pi RPC bridge stopped")


# ── 认证依赖 ────────────────────────────────────────────────────────

async def auth_required(authorization: str = Header(None)) -> dict:
    """JWT 认证中间件。X-API-Key 为简便模式（开发用）。"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    if authorization.startswith("Bearer "):
        token = authorization[7:]
        payload = verify_token(token)
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return payload

    # 本地开发模式：只接受显式配置的 API Key，不能接受任意 sk-*。
    if hmac.compare_digest(authorization, DEV_API_KEY):
        return {"sub": "dev-user", "auth_method": "api_key"}

    raise HTTPException(status_code=401, detail="Invalid authorization format")


# ── 请求模型 ────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    images: list[str] | None = None  # base64 图片（可选）


class TokenRequest(BaseModel):
    user_id: str
    password: str
    expires_hours: int = 24


# 研究工作流路由独立于 Pi 进程，即使模型服务暂时不可用也能管理任务。
from workflow_api import create_workflow_router
app.include_router(create_workflow_router(auth_required))


# ── API 路由 ────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    """健康检查。"""
    return {
        "status": "ok",
        "pi_cli": PI_CLI_PATH,
        "pi_ready": bool(bridge and bridge.proc),
        "workflow_ready": True,
    }


@app.post("/api/auth/token")
async def login(req: TokenRequest):
    """获取 JWT token，验证密码。"""
    from workflow_config import WorkflowConfig
    config = WorkflowConfig()
    if config.app_env == "test":
        user = config.test_users.get(req.user_id)
        if not user or user["password"] != req.password:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = create_token(req.user_id, req.expires_hours, role=user["role"])
    else:
        raise HTTPException(status_code=501, detail="Production auth not implemented")
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": req.expires_hours * 3600,
    }


@app.post("/api/chat")
async def chat(req: ChatRequest, user: dict = Depends(auth_required)):
    """发送消息并流式返回（SSE）。"""
    if not bridge or not bridge.proc:
        raise HTTPException(status_code=503, detail="Pi RPC bridge not ready")

    async def event_generator() -> AsyncIterator[str]:
        try:
            await bridge.send_cmd_no_wait("prompt", message=req.message)
            queue = bridge.event_stream()

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=300)
                    event_type = event.get("type", "unknown")

                    # 格式化 SSE
                    yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

                    if event_type == "agent_settled":
                        break
                except asyncio.TimeoutError:
                    yield f"event: timeout\ndata: {json.dumps({'message': 'Agent timeout'})}\n\n"
                    break
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat/sync")
async def chat_sync(req: ChatRequest, user: dict = Depends(auth_required)):
    """同步发送消息（阻塞等待完成）。"""
    if not bridge or not bridge.proc:
        raise HTTPException(status_code=503, detail="Pi RPC bridge not ready")

    events = []
    try:
        await bridge.send_cmd_no_wait("prompt", message=req.message)
        queue = bridge.event_stream()

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=300)
                events.append(event)
                if event.get("type") == "agent_settled":
                    break
            except asyncio.TimeoutError:
                events.append({"type": "timeout", "message": "Agent timeout"})
                break
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "events": events},
        )

    # 收集 assistant 回复文本
    assistant_text = ""
    for evt in events:
        if evt.get("type") == "message_update":
            delta = evt.get("delta", {})
            if delta.get("type") == "text_delta":
                assistant_text += delta.get("text", "")

    return {
        "events": events,
        "assistant_text": assistant_text,
        "event_count": len(events),
    }


@app.get("/api/sessions")
async def list_sessions(user: dict = Depends(auth_required)):
    """列出会话。"""
    if not bridge:
        return {"sessions": []}
    try:
        state = await bridge.get_state()
        session_file = state.get("sessionFile", "")
        return {
            "current_session": session_file,
            "model": state.get("model", ""),
            "message_count": state.get("messageCount", 0),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sessions/new")
async def new_session(user: dict = Depends(auth_required)):
    """创建新会话。"""
    if not bridge:
        raise HTTPException(status_code=503, detail="Pi RPC bridge not ready")
    try:
        result = await bridge.new_session()
        return {"status": "ok", "details": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(None)):
    """WebSocket 实时通信。"""
    if not token:
        await ws.close(code=4001, reason="Missing token")
        return

    payload = verify_token(token)
    if not payload and not hmac.compare_digest(token, DEV_API_KEY):
        await ws.close(code=4001, reason="Invalid token")
        return

    await ws.accept()

    if not bridge or not bridge.proc:
        await ws.send_json({"type": "error", "message": "Pi RPC bridge not ready"})
        await ws.close()
        return

    queue = bridge.event_stream()
    consumer_task: asyncio.Task | None = None

    async def forward_events():
        """从 Pi 事件队列读取并转发到 WebSocket。"""
        try:
            while True:
                event = await queue.get()
                await ws.send_json(event)
        except asyncio.CancelledError:
            pass
        except WebSocketDisconnect:
            pass

    consumer_task = asyncio.create_task(forward_events())

    try:
        while True:
            # 接收客户端消息
            data = await ws.receive_json()
            cmd_type = data.get("type", "prompt")

            if cmd_type == "prompt":
                await bridge.send_cmd_no_wait("prompt", message=data.get("message", ""))
            elif cmd_type == "steer":
                await bridge.send_cmd_no_wait("steer", message=data.get("message", ""))
            elif cmd_type == "abort":
                await bridge.send_cmd_no_wait("abort")
            elif cmd_type == "new_session":
                await bridge.new_session()
            elif cmd_type == "get_state":
                state = await bridge.get_state()
                await ws.send_json({"type": "state", "data": state})
            else:
                await ws.send_json({"type": "error", "message": f"Unknown command: {cmd_type}"})

    except WebSocketDisconnect:
        pass
    finally:
        if consumer_task:
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass


# ── Web UI 静态文件 ─────────────────────────────────────────────────

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")


@app.get("/", include_in_schema=False)
async def serve_index():
    """提供 Web UI 首页。"""
    index_path = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_path):
        from fastapi.responses import FileResponse
        return FileResponse(index_path)
    return JSONResponse({"error": "Web UI not found. Run from project root."}, status_code=404)


# ── 入口 ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Agent Workflow Web API Server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev)")
    args = parser.parse_args()

    print(f"[web-api] Pi CLI: {PI_CLI_PATH or 'NOT FOUND — set PI_CLI_PATH env var'}")
    print(f"[web-api] Starting on http://{args.host}:{args.port}")

    uvicorn.run(
        "web_api_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
