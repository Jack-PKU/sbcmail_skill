from __future__ import annotations

"""SBCMailbox — the core skill class.

Provides a high-level, drop-in mailbox for any AI agent. One line to install,
one line to init. Handles account creation, agent registration, credential
persistence, message sending, polling, auto-ACK, intent-based routing, and
background listening — all automatically.

Sync usage:
    from sbcmail_skill import SBCMailbox

    mailbox = SBCMailbox("my-agent")

    @mailbox.on("chat")
    def handle(msg):
        mailbox.reply(msg, intent="chat", payload={"body": "hi"})

    mailbox.run()  # blocks, polls in a loop

Async usage:
    from sbcmail_skill import SBCMailbox

    mailbox = SBCMailbox("my-agent")

    @mailbox.on("chat")
    async def handle(msg):
        await mailbox.async_reply(msg, intent="chat", payload={"body": "hi"})

    await mailbox.run_async()
"""

import asyncio
import inspect
import logging
import signal
import time
from typing import Callable, Optional, Union

import httpx

from .models import PollMessage, SendResult
from .credentials import clear_credentials, load_credentials, save_credentials

logger = logging.getLogger("sbcmail.skill")

# Default SBCMail server — change this to your deployment URL
DEFAULT_BASE_URL = "https://api.sbcmail.ai"


class SBCMailbox:
    """Drop-in mailbox for any AI agent.

    Args:
        agent_id: Unique name for this agent (becomes <agent_id>@<node>).
        base_url: SBCMail server URL. Default: http://localhost:8000
        api_key: Existing API key. If None, auto-creates account on first use.
        webhook_url: Optional webhook URL for push delivery.
        poll_interval: Seconds between poll cycles (default 2).
        auto_ack: Automatically ACK messages after handler completes (default True).
        supported_intents: List of intents this agent handles.
    """

    def __init__(
        self,
        agent_id: str,
        base_url: str = DEFAULT_BASE_URL,
        api_key: Optional[str] = None,
        webhook_url: Optional[str] = None,
        poll_interval: float = 2.0,
        auto_ack: bool = True,
        supported_intents: Optional[list[str]] = None,
    ):
        self.agent_id = agent_id
        self.base_url = base_url.rstrip("/")
        self.poll_interval = poll_interval
        self.auto_ack = auto_ack
        self.webhook_url = webhook_url
        self.supported_intents = supported_intents

        self._handlers: dict[str, Callable] = {}
        self._default_handler: Optional[Callable] = None
        self._address: Optional[str] = None
        self._api_key: Optional[str] = None
        self._private_key: Optional[str] = None
        self._http: Optional[httpx.Client] = None
        self._async_http: Optional[httpx.AsyncClient] = None
        self._running = False
        self._last_poll_time: Optional[str] = None  # ISO timestamp for incremental sync

        self._init_credentials(api_key)

    def _init_credentials(self, api_key: Optional[str]) -> None:
        """Load or create credentials."""
        saved = load_credentials(self.agent_id)
        if saved and saved.get("base_url") == self.base_url:
            self._api_key = saved["api_key"]
            self._address = saved["address"]
            self._private_key = saved.get("private_key")
            logger.info(f"Restored credentials for {self._address}")
            return

        if api_key:
            self._api_key = api_key
            return

        logger.info(f"No saved credentials for '{self.agent_id}', will auto-register on connect.")

    def _get_http(self) -> httpx.Client:
        if self._http is None:
            self._ensure_registered_sync()
            self._http = httpx.Client(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=30.0,
            )
        return self._http

    def _get_async_http(self) -> httpx.AsyncClient:
        if self._async_http is None:
            self._async_http = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=30.0,
            )
        return self._async_http

    # -- Auto-registration ------------------------------------------------

    def _ensure_registered_sync(self) -> None:
        """Create account + register agent if needed (sync)."""
        if self._address:
            return

        if not self._api_key:
            logger.info("Creating new SBCMail account...")
            resp = httpx.post(f"{self.base_url}/v1/accounts/create", timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
            self._api_key = data["api_key"]
            logger.info(f"Account created: {data['account_id']}")

        logger.info(f"Registering agent '{self.agent_id}'...")
        body: dict = {"agent_id": self.agent_id}
        if self.webhook_url:
            body["webhook_url"] = self.webhook_url
        if self.supported_intents:
            body["supported_intents"] = self.supported_intents

        headers = {"Authorization": f"Bearer {self._api_key}"}
        resp = httpx.post(
            f"{self.base_url}/v1/agents/register",
            json=body,
            headers=headers,
            timeout=30.0,
        )

        if resp.status_code == 409:
            self._address = f"{self.agent_id}@{self.base_url.split('/')[-1]}"
            info_resp = httpx.get(
                f"{self.base_url}/v1/agents/{self.agent_id}@agentmail.ai/info",
                timeout=30.0,
            )
            if info_resp.status_code == 200:
                self._address = info_resp.json()["address"]
            else:
                self._address = f"{self.agent_id}@agentmail.ai"

            save_credentials(self.agent_id, {
                "base_url": self.base_url,
                "api_key": self._api_key,
                "address": self._address,
                "private_key": self._private_key,
                "agent_id": self.agent_id,
            })
            logger.info(f"Agent already registered, reusing: {self._address}")
            return

        resp.raise_for_status()
        agent_data = resp.json()
        self._address = agent_data["address"]
        self._private_key = agent_data.get("private_key")

        save_credentials(self.agent_id, {
            "base_url": self.base_url,
            "api_key": self._api_key,
            "address": self._address,
            "private_key": self._private_key,
            "agent_id": self.agent_id,
        })
        logger.info(f"Agent registered: {self._address}")

    async def _ensure_registered_async(self) -> None:
        """Create account + register agent if needed (async)."""
        if self._address:
            return

        if not self._api_key:
            logger.info("Creating new SBCMail account...")
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"{self.base_url}/v1/accounts/create")
                resp.raise_for_status()
                data = resp.json()
                self._api_key = data["api_key"]
                logger.info(f"Account created: {data['account_id']}")

        logger.info(f"Registering agent '{self.agent_id}'...")
        body: dict = {"agent_id": self.agent_id}
        if self.webhook_url:
            body["webhook_url"] = self.webhook_url
        if self.supported_intents:
            body["supported_intents"] = self.supported_intents

        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {"Authorization": f"Bearer {self._api_key}"}
            resp = await client.post(
                f"{self.base_url}/v1/agents/register",
                json=body,
                headers=headers,
            )

            if resp.status_code == 409:
                info_resp = await client.get(
                    f"{self.base_url}/v1/agents/{self.agent_id}@agentmail.ai/info",
                )
                if info_resp.status_code == 200:
                    self._address = info_resp.json()["address"]
                else:
                    self._address = f"{self.agent_id}@agentmail.ai"

                save_credentials(self.agent_id, {
                    "base_url": self.base_url,
                    "api_key": self._api_key,
                    "address": self._address,
                    "private_key": self._private_key,
                    "agent_id": self.agent_id,
                })
                logger.info(f"Agent already registered, reusing: {self._address}")
                return

            resp.raise_for_status()
            agent_data = resp.json()
            self._address = agent_data["address"]
            self._private_key = agent_data.get("private_key")

        save_credentials(self.agent_id, {
            "base_url": self.base_url,
            "api_key": self._api_key,
            "address": self._address,
            "private_key": self._private_key,
            "agent_id": self.agent_id,
        })
        logger.info(f"Agent registered: {self._address}")

    # -- Properties --------------------------------------------------------

    @property
    def address(self) -> str:
        """This agent's full address (e.g. my-agent@sbcmail.ai)."""
        if not self._address:
            self._ensure_registered_sync()
        return self._address

    # -- Handler registration ----------------------------------------------

    def on(self, intent: str) -> Callable:
        """Decorator to register a handler for a specific intent.

        @mailbox.on("chat")
        def handle_chat(msg):
            print(msg.payload)
        """
        def decorator(fn: Callable) -> Callable:
            self._handlers[intent] = fn
            return fn
        return decorator

    def on_default(self, fn: Callable) -> Callable:
        """Decorator to register a fallback handler for unmatched intents.

        @mailbox.on_default
        def fallback(msg):
            print(f"Unknown intent: {msg.intent}")
        """
        self._default_handler = fn
        return fn

    # -- Sending -----------------------------------------------------------

    def send(
        self,
        to: Union[str, list[str]],
        intent: str,
        payload: Optional[dict] = None,
        thread_id: Optional[str] = None,
        reply_to: Optional[str] = None,
        priority: str = "normal",
        ttl: Optional[int] = None,
    ) -> SendResult:
        """Send a message (sync)."""
        http = self._get_http()
        body: dict = {
            "sender": self.address,
            "to": to,
            "intent": intent,
            "payload": payload or {},
        }
        if thread_id:
            body["thread_id"] = thread_id
        if reply_to:
            body["reply_to"] = reply_to
        if priority != "normal":
            body["priority"] = priority
        if ttl is not None:
            body["ttl"] = ttl

        resp = http.post("/v1/messages/send", json=body)
        resp.raise_for_status()
        return SendResult(**resp.json())

    async def async_send(
        self,
        to: Union[str, list[str]],
        intent: str,
        payload: Optional[dict] = None,
        thread_id: Optional[str] = None,
        reply_to: Optional[str] = None,
        priority: str = "normal",
        ttl: Optional[int] = None,
    ) -> SendResult:
        """Send a message (async)."""
        await self._ensure_registered_async()
        http = self._get_async_http()
        body: dict = {
            "sender": self._address,
            "to": to,
            "intent": intent,
            "payload": payload or {},
        }
        if thread_id:
            body["thread_id"] = thread_id
        if reply_to:
            body["reply_to"] = reply_to
        if priority != "normal":
            body["priority"] = priority
        if ttl is not None:
            body["ttl"] = ttl

        resp = await http.post("/v1/messages/send", json=body)
        resp.raise_for_status()
        return SendResult(**resp.json())

    def reply(
        self,
        original: PollMessage,
        intent: str,
        payload: Optional[dict] = None,
        priority: str = "normal",
    ) -> SendResult:
        """Reply to a received message (sync). Auto-sets thread_id and reply_to."""
        return self.send(
            to=original.sender,
            intent=intent,
            payload=payload,
            thread_id=original.thread_id,
            reply_to=original.message_id,
            priority=priority,
        )

    async def async_reply(
        self,
        original: PollMessage,
        intent: str,
        payload: Optional[dict] = None,
        priority: str = "normal",
    ) -> SendResult:
        """Reply to a received message (async)."""
        return await self.async_send(
            to=original.sender,
            intent=intent,
            payload=payload,
            thread_id=original.thread_id,
            reply_to=original.message_id,
            priority=priority,
        )

    # -- Attachments -------------------------------------------------------

    def send_with_attachments(
        self,
        to: Union[str, list[str]],
        intent: str,
        payload: Optional[dict] = None,
        attachments: Optional[list[dict]] = None,
        thread_id: Optional[str] = None,
        reply_to: Optional[str] = None,
        priority: str = "normal",
        ttl: Optional[int] = None,
    ) -> SendResult:
        """Send a message with file attachments (sync).

        attachments: list of dicts, each with:
            - path: str (file path) OR
            - data: bytes (raw bytes) OR
            - content: str (text content)
            AND:
            - filename: str (required)
            - content_type: str (optional, auto-detected from filename)
        """
        encoded = self._encode_attachments(attachments or [])
        http = self._get_http()
        body: dict = {
            "sender": self.address,
            "to": to,
            "intent": intent,
            "payload": payload or {},
            "attachments": encoded,
        }
        if thread_id:
            body["thread_id"] = thread_id
        if reply_to:
            body["reply_to"] = reply_to
        if priority != "normal":
            body["priority"] = priority
        if ttl is not None:
            body["ttl"] = ttl

        resp = http.post("/v1/messages/send", json=body)
        resp.raise_for_status()
        return SendResult(**resp.json())

    async def async_send_with_attachments(
        self,
        to: Union[str, list[str]],
        intent: str,
        payload: Optional[dict] = None,
        attachments: Optional[list[dict]] = None,
        thread_id: Optional[str] = None,
        reply_to: Optional[str] = None,
        priority: str = "normal",
        ttl: Optional[int] = None,
    ) -> SendResult:
        """Send a message with file attachments (async).

        attachments: list of dicts, each with:
            - path: str (file path) OR
            - data: bytes (raw bytes) OR
            - content: str (text content)
            AND:
            - filename: str (required)
            - content_type: str (optional, auto-detected from filename)
        """
        encoded = self._encode_attachments(attachments or [])
        await self._ensure_registered_async()
        http = self._get_async_http()
        body: dict = {
            "sender": self._address,
            "to": to,
            "intent": intent,
            "payload": payload or {},
            "attachments": encoded,
        }
        if thread_id:
            body["thread_id"] = thread_id
        if reply_to:
            body["reply_to"] = reply_to
        if priority != "normal":
            body["priority"] = priority
        if ttl is not None:
            body["ttl"] = ttl

        resp = await http.post("/v1/messages/send", json=body)
        resp.raise_for_status()
        return SendResult(**resp.json())

    @staticmethod
    def _encode_attachments(attachments: list[dict]) -> list[dict]:
        """Encode attachments to the API format (base64 data)."""
        import base64
        import mimetypes

        encoded = []
        for att in attachments:
            if "filename" not in att:
                raise ValueError("Each attachment must have a 'filename' key")

            # Determine raw bytes from one of the supported input formats
            if "path" in att:
                with open(att["path"], "rb") as f:
                    raw = f.read()
            elif "data" in att and isinstance(att["data"], bytes):
                raw = att["data"]
            elif "content" in att:
                raw = att["content"].encode("utf-8")
            else:
                raise ValueError(
                    f"Attachment '{att['filename']}' must provide 'path', 'data' (bytes), or 'content' (str)"
                )

            # Auto-detect content_type if not provided
            content_type = att.get("content_type")
            if not content_type:
                mime, _ = mimetypes.guess_type(att["filename"])
                content_type = mime or "application/octet-stream"

            encoded.append({
                "filename": att["filename"],
                "content_type": content_type,
                "data": base64.b64encode(raw).decode("ascii"),
            })

        return encoded

    @staticmethod
    def get_attachments(msg) -> list[dict]:
        """Extract attachments from a received message's payload.

        Returns a list of dicts with keys: filename, content_type, data (base64 str).
        Returns an empty list if the message has no attachments.
        """
        payload = msg.payload if hasattr(msg, "payload") else msg
        if isinstance(payload, dict):
            return payload.get("_attachments", [])
        return []

    @staticmethod
    def decode_attachment(attachment: dict) -> bytes:
        """Decode a base64-encoded attachment to raw bytes."""
        import base64
        return base64.b64decode(attachment["data"])

    @staticmethod
    def save_attachment(attachment: dict, directory: str = ".") -> str:
        """Decode and save an attachment to disk. Returns the file path."""
        import base64
        import os
        filepath = os.path.join(directory, attachment["filename"])
        raw = base64.b64decode(attachment["data"])
        with open(filepath, "wb") as f:
            f.write(raw)
        return filepath

    # -- Polling -----------------------------------------------------------

    def poll(self, limit: int = 20, since: Optional[str] = None) -> list[PollMessage]:
        """Poll for new messages (sync).

        Args:
            limit: Max messages to return.
            since: ISO timestamp — only return messages after this time.
                   If None and incremental sync is active, uses last poll time.
        """
        http = self._get_http()
        params: dict = {"limit": limit}
        effective_since = since or self._last_poll_time
        if effective_since:
            params["since"] = effective_since

        resp = http.get(f"/v1/agents/{self.address}/messages/poll", params=params)
        resp.raise_for_status()
        data = resp.json()
        messages = [PollMessage(**m) for m in data["messages"]]

        # Update sync watermark
        if messages:
            self._last_poll_time = messages[-1].created_at.isoformat() if messages[-1].created_at else None

        return messages

    async def async_poll(self, limit: int = 20, since: Optional[str] = None) -> list[PollMessage]:
        """Poll for new messages (async).

        Args:
            limit: Max messages to return.
            since: ISO timestamp — only return messages after this time.
                   If None and incremental sync is active, uses last poll time.
        """
        await self._ensure_registered_async()
        http = self._get_async_http()
        params: dict = {"limit": limit}
        effective_since = since or self._last_poll_time
        if effective_since:
            params["since"] = effective_since

        resp = await http.get(
            f"/v1/agents/{self._address}/messages/poll", params=params
        )
        resp.raise_for_status()
        data = resp.json()
        messages = [PollMessage(**m) for m in data["messages"]]

        # Update sync watermark
        if messages:
            self._last_poll_time = messages[-1].created_at.isoformat() if messages[-1].created_at else None

        return messages

    def ack(self, message_id: str) -> None:
        """Acknowledge a message (sync)."""
        http = self._get_http()
        resp = http.post(f"/v1/agents/{self.address}/messages/{message_id}/ack")
        resp.raise_for_status()

    async def async_ack(self, message_id: str) -> None:
        """Acknowledge a message (async)."""
        http = self._get_async_http()
        resp = await http.post(f"/v1/agents/{self._address}/messages/{message_id}/ack")
        resp.raise_for_status()

    # -- Run loop ----------------------------------------------------------

    def _dispatch(self, msg: PollMessage) -> None:
        """Route message to handler (sync)."""
        handler = self._handlers.get(msg.intent, self._default_handler)
        if handler is None:
            logger.warning(f"No handler for intent '{msg.intent}', skipping")
            return
        try:
            result = handler(msg)
            if inspect.isawaitable(result):
                logger.warning(
                    f"Handler for '{msg.intent}' is async but mailbox.run() is sync. "
                    "Use mailbox.run_async() instead."
                )
        except Exception:
            logger.exception(f"Handler error for intent '{msg.intent}'")
            return

        if self.auto_ack:
            try:
                self.ack(msg.message_id)
            except Exception:
                logger.exception(f"Failed to ACK {msg.message_id}")

    async def _async_dispatch(self, msg: PollMessage) -> None:
        """Route message to handler (async)."""
        handler = self._handlers.get(msg.intent, self._default_handler)
        if handler is None:
            logger.warning(f"No handler for intent '{msg.intent}', skipping")
            return
        try:
            result = handler(msg)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception(f"Handler error for intent '{msg.intent}'")
            return

        if self.auto_ack:
            try:
                await self.async_ack(msg.message_id)
            except Exception:
                logger.exception(f"Failed to ACK {msg.message_id}")

    def run(self) -> None:
        """Start the sync polling loop (blocks forever). Press Ctrl+C to stop."""
        self._running = True
        logger.info(f"SBCMailbox running: {self.address} (poll every {self.poll_interval}s)")
        logger.info(f"Registered handlers: {list(self._handlers.keys()) or ['(none)']}")

        def _stop(*_):
            self._running = False
            logger.info("Shutting down mailbox...")

        try:
            signal.signal(signal.SIGINT, _stop)
            signal.signal(signal.SIGTERM, _stop)
        except ValueError:
            pass

        while self._running:
            try:
                messages = self.poll()
                for msg in messages:
                    self._dispatch(msg)
            except httpx.HTTPError as e:
                logger.error(f"Poll error: {e}")
            except Exception:
                logger.exception("Unexpected error in poll loop")

            if self._running:
                time.sleep(self.poll_interval)

        self.close()
        logger.info("Mailbox stopped.")

    async def run_async(self) -> None:
        """Start the async polling loop. Use: await mailbox.run_async()"""
        await self._ensure_registered_async()
        self._running = True
        logger.info(f"SBCMailbox running: {self._address} (poll every {self.poll_interval}s)")
        logger.info(f"Registered handlers: {list(self._handlers.keys()) or ['(none)']}")

        while self._running:
            try:
                messages = await self.async_poll()
                for msg in messages:
                    await self._async_dispatch(msg)
            except httpx.HTTPError as e:
                logger.error(f"Poll error: {e}")
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unexpected error in poll loop")

            if self._running:
                await asyncio.sleep(self.poll_interval)

        await self.async_close()
        logger.info("Mailbox stopped.")

    def stop(self) -> None:
        """Signal the polling loop to stop."""
        self._running = False

    # -- Utility -----------------------------------------------------------

    def get_message(self, message_id: str) -> MessageDetail:
        """Get full message detail including delivery status (sync)."""
        from .models import MessageDetail
        http = self._get_http()
        resp = http.get(f"/v1/messages/{message_id}")
        resp.raise_for_status()
        return MessageDetail(**resp.json())

    def get_thread(self, thread_id: str) -> list[PollMessage]:
        """Get all messages in a thread (sync)."""
        http = self._get_http()
        resp = http.get(f"/v1/threads/{thread_id}", params={"limit": 100})
        resp.raise_for_status()
        data = resp.json()
        return [PollMessage(**m) for m in data["messages"]]

    def search_messages(self, query: str, limit: int = 20) -> list[PollMessage]:
        """Search your messages by keyword (sync).

        Searches across sender, intent, and payload fields.
        """
        http = self._get_http()
        resp = http.get("/v1/messages/search", params={"q": query, "limit": limit})
        resp.raise_for_status()
        data = resp.json()
        return [PollMessage(**m) for m in data["messages"]]

    def search_agents(self, intent: str) -> list[str]:
        """Search for agents that support a given intent (sync)."""
        http = self._get_http()
        resp = http.get("/v1/agents/search", params={"intent": intent})
        resp.raise_for_status()
        return resp.json().get("agents", [])

    def reset(self) -> None:
        """Clear saved credentials and close connections."""
        clear_credentials(self.agent_id)
        self._address = None
        self._api_key = None
        self.close()

    def close(self) -> None:
        """Close sync HTTP client."""
        if self._http:
            self._http.close()
            self._http = None

    async def async_close(self) -> None:
        """Close async HTTP client."""
        if self._async_http:
            await self._async_http.aclose()
            self._async_http = None

    def __repr__(self) -> str:
        addr = self._address or f"{self.agent_id}@(not yet registered)"
        return f"SBCMailbox({addr})"
