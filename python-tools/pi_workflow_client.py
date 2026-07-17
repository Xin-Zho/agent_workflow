"""Pi RPC client for workflow stages. Manages a pi --mode rpc subprocess
and provides a simple prompt->response interface with timeout."""

import asyncio
import json
import os
import uuid
import logging

logger = logging.getLogger(__name__)


class PiWorkflowClient:
    """Manages a Pi RPC subprocess for workflow task execution.

    Unlike the Web API bridge, this client is designed for single-task use:
    send a structured prompt, wait for agent_settled, collect the response,
    and return. No streaming, no WebSocket, no multi-user multiplexing.
    """

    def __init__(self, pi_command: str = "pi", timeout: int = 300, cwd: str | None = None):
        self.pi_command = pi_command
        self.timeout = timeout
        self.cwd = cwd or os.getcwd()
        self.proc: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._lock = asyncio.Lock()

    async def start(self):
        """Start the Pi RPC subprocess."""
        if self.proc is not None:
            return

        if os.path.exists(self.pi_command):
            cmd = ["node", self.pi_command, "--mode", "rpc"]
        else:
            cmd = [self.pi_command, "--mode", "rpc"]

        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
        )

    async def stop(self):
        """Stop the Pi RPC subprocess."""
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

    async def send_prompt(self, prompt: str, session_id: str | None = None) -> str:
        """Send a prompt to Pi and wait for the final response.

        Returns the collected assistant text (all message_update deltas concatenated).
        Raises asyncio.TimeoutError if no agent_settled within timeout.
        """
        if not self.proc:
            await self.start()

        async with self._lock:
            self._request_id += 1
            req_id = self._request_id

            # Send prompt command
            cmd: dict = {"id": req_id, "type": "prompt", "message": prompt}
            if session_id:
                cmd["sessionId"] = session_id
            self.proc.stdin.write((json.dumps(cmd) + "\n").encode())
            await self.proc.stdin.drain()

            # Collect response events until agent_settled
            assistant_text = ""
            while True:
                try:
                    line = await asyncio.wait_for(
                        self.proc.stdout.readline(), timeout=self.timeout
                    )
                except asyncio.TimeoutError:
                    raise asyncio.TimeoutError(
                        f"Pi did not settle within {self.timeout}s"
                    )

                if not line:
                    raise RuntimeError("Pi RPC subprocess exited unexpectedly")

                try:
                    msg = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "agent_settled":
                    break

                if msg_type == "message_update":
                    delta = msg.get("delta", {})
                    if delta.get("type") == "text_delta":
                        assistant_text += delta.get("text", "")

                if msg_type == "error":
                    raise RuntimeError(f"Pi error: {msg.get('message', 'unknown')}")

            return assistant_text

    async def new_session(self, parent_session: str | None = None) -> str:
        """Create a new Pi session and return its ID."""
        if not self.proc:
            await self.start()

        async with self._lock:
            self._request_id += 1
            req_id = self._request_id
            cmd: dict = {"id": req_id, "type": "new_session"}
            if parent_session:
                cmd["parentSession"] = parent_session

            self.proc.stdin.write((json.dumps(cmd) + "\n").encode())
            await self.proc.stdin.drain()

            while True:
                line = await asyncio.wait_for(
                    self.proc.stdout.readline(), timeout=30
                )
                if not line:
                    raise RuntimeError("Pi RPC subprocess exited unexpectedly")
                try:
                    msg = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == req_id:
                    return msg.get("data", {}).get("sessionId", "")
