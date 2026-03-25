from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import threading
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, DefaultDict, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

OWNER_TTL_SECONDS = 60
OWNER_RENEW_INTERVAL_SECONDS = 20


class EventSubscription(ABC):
    @abstractmethod
    async def get(self, timeout: Optional[float] = None) -> Any:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


class AsyncQueueSubscription(EventSubscription):
    def __init__(self, queue: asyncio.Queue, *, close_cb: Optional[Callable[[], Awaitable[None]]] = None) -> None:
        self._queue = queue
        self._close_cb = close_cb

    async def get(self, timeout: Optional[float] = None) -> Any:
        if timeout is None:
            return await self._queue.get()
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)

    async def close(self) -> None:
        if self._close_cb is not None:
            await self._close_cb()


class RealtimeBus(ABC):
    @abstractmethod
    async def publish_run_event(self, run_id: str, seq: int, payload: Dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def subscribe_run_events(self, run_id: str) -> EventSubscription:
        raise NotImplementedError

    @abstractmethod
    async def publish_job_event(self, job_id: str, payload: Dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def subscribe_job_events(self, job_id: str) -> EventSubscription:
        raise NotImplementedError

    @abstractmethod
    async def register_owner(self, kind: str, entity_id: str, worker_id: str, *, ttl_seconds: int = OWNER_TTL_SECONDS) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_owner(self, kind: str, entity_id: str) -> Optional[str]:
        raise NotImplementedError

    @abstractmethod
    async def clear_owner(self, kind: str, entity_id: str, *, worker_id: Optional[str] = None) -> None:
        raise NotImplementedError

    @abstractmethod
    async def send_control(self, worker_id: str, message: Dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def subscribe_controls(self, worker_id: str) -> EventSubscription:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


class InMemoryRealtimeBus(RealtimeBus):
    def __init__(self) -> None:
        self._run_subscribers: DefaultDict[str, List[asyncio.Queue]] = defaultdict(list)
        self._job_subscribers: DefaultDict[str, List[asyncio.Queue]] = defaultdict(list)
        self._control_subscribers: DefaultDict[str, List[asyncio.Queue]] = defaultdict(list)
        self._owners: Dict[Tuple[str, str], Tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def publish_run_event(self, run_id: str, seq: int, payload: Dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._run_subscribers.get(run_id, []))
        for queue in queues:
            await self._safe_put(queue, (seq, payload))

    async def subscribe_run_events(self, run_id: str) -> EventSubscription:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._run_subscribers[run_id].append(queue)

        async def _close() -> None:
            async with self._lock:
                queues = self._run_subscribers.get(run_id, [])
                with contextlib.suppress(ValueError):
                    queues.remove(queue)
                if not queues:
                    self._run_subscribers.pop(run_id, None)

        return AsyncQueueSubscription(queue, close_cb=_close)

    async def publish_job_event(self, job_id: str, payload: Dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._job_subscribers.get(job_id, []))
        for queue in queues:
            await self._safe_put(queue, payload)

    async def subscribe_job_events(self, job_id: str) -> EventSubscription:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._job_subscribers[job_id].append(queue)

        async def _close() -> None:
            async with self._lock:
                queues = self._job_subscribers.get(job_id, [])
                with contextlib.suppress(ValueError):
                    queues.remove(queue)
                if not queues:
                    self._job_subscribers.pop(job_id, None)

        return AsyncQueueSubscription(queue, close_cb=_close)

    async def register_owner(self, kind: str, entity_id: str, worker_id: str, *, ttl_seconds: int = OWNER_TTL_SECONDS) -> None:
        async with self._lock:
            self._owners[(kind, entity_id)] = (worker_id, asyncio.get_running_loop().time() + ttl_seconds)

    async def get_owner(self, kind: str, entity_id: str) -> Optional[str]:
        async with self._lock:
            value = self._owners.get((kind, entity_id))
            if value is None:
                return None
            worker_id, expires_at = value
            if asyncio.get_running_loop().time() >= expires_at:
                self._owners.pop((kind, entity_id), None)
                return None
            return worker_id

    async def clear_owner(self, kind: str, entity_id: str, *, worker_id: Optional[str] = None) -> None:
        async with self._lock:
            current = self._owners.get((kind, entity_id))
            if current is None:
                return
            if worker_id is not None and current[0] != worker_id:
                return
            self._owners.pop((kind, entity_id), None)

    async def send_control(self, worker_id: str, message: Dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._control_subscribers.get(worker_id, []))
        for queue in queues:
            await self._safe_put(queue, message)

    async def subscribe_controls(self, worker_id: str) -> EventSubscription:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._control_subscribers[worker_id].append(queue)

        async def _close() -> None:
            async with self._lock:
                queues = self._control_subscribers.get(worker_id, [])
                with contextlib.suppress(ValueError):
                    queues.remove(queue)
                if not queues:
                    self._control_subscribers.pop(worker_id, None)

        return AsyncQueueSubscription(queue, close_cb=_close)

    async def close(self) -> None:
        async with self._lock:
            self._run_subscribers.clear()
            self._job_subscribers.clear()
            self._control_subscribers.clear()
            self._owners.clear()

    @staticmethod
    async def _safe_put(queue: asyncio.Queue, value: Any) -> None:
        if queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        await queue.put(value)


class RedisPubSubSubscription(EventSubscription):
    def __init__(
        self,
        *,
        pubsub: Any,
        channel: str,
        parser: Callable[[str], Any],
    ) -> None:
        self._pubsub = pubsub
        self._channel = channel
        self._parser = parser
        self._queue: asyncio.Queue = asyncio.Queue()
        self._closed = False
        self._reader_task = asyncio.create_task(self._reader())

    async def _reader(self) -> None:
        try:
            await self._pubsub.subscribe(self._channel)
            while not self._closed:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message is None:
                    await asyncio.sleep(0.05)
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    text = data.decode("utf-8", errors="replace")
                else:
                    text = str(data)
                await self._queue.put(self._parser(text))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Realtime Redis subscription failed for %s: %s", self._channel, exc)
        finally:
            with contextlib.suppress(Exception):
                await self._pubsub.unsubscribe(self._channel)
            with contextlib.suppress(Exception):
                await self._pubsub.close()

    async def get(self, timeout: Optional[float] = None) -> Any:
        if timeout is None:
            return await self._queue.get()
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)

    async def close(self) -> None:
        self._closed = True
        self._reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._reader_task


class RedisRealtimeBus(RealtimeBus):
    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis = None

    async def _client(self):
        if self._redis is None:
            try:
                from redis.asyncio import Redis
            except Exception as exc:  # pragma: no cover
                raise RuntimeError("redis package is required for RedisRealtimeBus") from exc
            self._redis = Redis.from_url(self._redis_url, encoding="utf-8", decode_responses=False)
        return self._redis

    @staticmethod
    def _owner_key(kind: str, entity_id: str) -> str:
        return f"rt:owner:{kind}:{entity_id}"

    @staticmethod
    def _run_channel(run_id: str) -> str:
        return f"rt:run:{run_id}:events"

    @staticmethod
    def _job_channel(job_id: str) -> str:
        return f"rt:job:{job_id}:events"

    @staticmethod
    def _worker_control_channel(worker_id: str) -> str:
        return f"rt:worker:{worker_id}:control"

    async def publish_run_event(self, run_id: str, seq: int, payload: Dict[str, Any]) -> None:
        client = await self._client()
        await client.publish(
            self._run_channel(run_id),
            json.dumps({"seq": seq, "payload": payload}, ensure_ascii=False),
        )

    async def subscribe_run_events(self, run_id: str) -> EventSubscription:
        client = await self._client()
        pubsub = client.pubsub()
        return RedisPubSubSubscription(
            pubsub=pubsub,
            channel=self._run_channel(run_id),
            parser=lambda text: _parse_run_message(text),
        )

    async def publish_job_event(self, job_id: str, payload: Dict[str, Any]) -> None:
        client = await self._client()
        await client.publish(
            self._job_channel(job_id),
            json.dumps(payload, ensure_ascii=False),
        )

    async def subscribe_job_events(self, job_id: str) -> EventSubscription:
        client = await self._client()
        pubsub = client.pubsub()
        return RedisPubSubSubscription(
            pubsub=pubsub,
            channel=self._job_channel(job_id),
            parser=lambda text: json.loads(text),
        )

    async def register_owner(self, kind: str, entity_id: str, worker_id: str, *, ttl_seconds: int = OWNER_TTL_SECONDS) -> None:
        client = await self._client()
        await client.set(self._owner_key(kind, entity_id), worker_id, ex=ttl_seconds)

    async def get_owner(self, kind: str, entity_id: str) -> Optional[str]:
        client = await self._client()
        value = await client.get(self._owner_key(kind, entity_id))
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    async def clear_owner(self, kind: str, entity_id: str, *, worker_id: Optional[str] = None) -> None:
        client = await self._client()
        key = self._owner_key(kind, entity_id)
        if worker_id is None:
            await client.delete(key)
            return
        current = await self.get_owner(kind, entity_id)
        if current == worker_id:
            await client.delete(key)

    async def send_control(self, worker_id: str, message: Dict[str, Any]) -> None:
        client = await self._client()
        await client.publish(
            self._worker_control_channel(worker_id),
            json.dumps(message, ensure_ascii=False),
        )

    async def subscribe_controls(self, worker_id: str) -> EventSubscription:
        client = await self._client()
        pubsub = client.pubsub()
        return RedisPubSubSubscription(
            pubsub=pubsub,
            channel=self._worker_control_channel(worker_id),
            parser=lambda text: json.loads(text),
        )

    async def close(self) -> None:
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.close()
            self._redis = None


def _parse_run_message(text: str) -> Tuple[int, Dict[str, Any]]:
    raw = json.loads(text)
    seq = int(raw.get("seq", -1))
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        payload = {"type": "unknown", "raw": payload}
    return seq, payload


_realtime_bus: Optional[RealtimeBus] = None
_worker_id = str(
    os.getenv("REALTIME_WORKER_ID")
    or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
)
_app_loop: Optional[asyncio.AbstractEventLoop] = None
_control_consumer_task: Optional[asyncio.Task[None]] = None
_control_ack_waiters: Dict[str, asyncio.Future[bool]] = {}
_owner_lease_tasks: Dict[Tuple[str, str], asyncio.Task[None]] = {}
_owner_lease_lock = threading.Lock()


def get_worker_id() -> str:
    return _worker_id


def get_realtime_backend_name() -> str:
    backend = str(os.getenv("REALTIME_BUS_BACKEND") or "").strip().lower()
    if backend:
        return backend
    if os.getenv("REALTIME_REDIS_URL") or os.getenv("REDIS_URL"):
        return "redis"
    return "memory"


async def get_realtime_bus() -> RealtimeBus:
    global _realtime_bus
    if _realtime_bus is None:
        backend = get_realtime_backend_name()
        if backend == "redis":
            redis_url = str(os.getenv("REALTIME_REDIS_URL") or os.getenv("REDIS_URL") or "").strip()
            if not redis_url:
                raise RuntimeError("REALTIME_REDIS_URL or REDIS_URL is required for redis backend")
            _realtime_bus = RedisRealtimeBus(redis_url)
        else:
            _realtime_bus = InMemoryRealtimeBus()
    return _realtime_bus


async def init_realtime_bus() -> None:
    global _app_loop, _control_consumer_task
    _app_loop = asyncio.get_running_loop()
    await get_realtime_bus()
    if _control_consumer_task is None or _control_consumer_task.done():
        _control_consumer_task = asyncio.create_task(_control_consumer())


async def close_realtime_bus() -> None:
    global _realtime_bus, _control_consumer_task, _app_loop
    if _control_consumer_task is not None:
        _control_consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _control_consumer_task
        _control_consumer_task = None
    for waiter in list(_control_ack_waiters.values()):
        if not waiter.done():
            waiter.cancel()
    _control_ack_waiters.clear()
    for key, task in list(_owner_lease_tasks.items()):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        _owner_lease_tasks.pop(key, None)
    if _realtime_bus is not None:
        await _realtime_bus.close()
        _realtime_bus = None
    _app_loop = None


def submit_async(coro: Awaitable[Any]) -> None:
    if _app_loop is None:
        raise RuntimeError("Realtime bus is not initialised")
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    if current is _app_loop:
        asyncio.create_task(coro)
    else:
        asyncio.run_coroutine_threadsafe(coro, _app_loop)


def start_owner_lease(kind: str, entity_id: str) -> None:
    key = (kind, entity_id)

    async def _lease() -> None:
        bus = await get_realtime_bus()
        await bus.register_owner(kind, entity_id, _worker_id, ttl_seconds=OWNER_TTL_SECONDS)
        try:
            while True:
                await asyncio.sleep(OWNER_RENEW_INTERVAL_SECONDS)
                await bus.register_owner(kind, entity_id, _worker_id, ttl_seconds=OWNER_TTL_SECONDS)
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib.suppress(Exception):
                await bus.clear_owner(kind, entity_id, worker_id=_worker_id)

    with _owner_lease_lock:
        existing = _owner_lease_tasks.get(key)
        if existing is not None and not existing.done():
            return
        if _app_loop is None:
            return
        if _app_loop.is_closed():
            return
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is _app_loop:
            task = asyncio.create_task(_lease())
        else:
            future = asyncio.run_coroutine_threadsafe(_create_task(_lease()), _app_loop)
            task = future.result()
        _owner_lease_tasks[key] = task


async def _create_task(coro: Awaitable[None]) -> asyncio.Task[None]:
    return asyncio.create_task(coro)


def stop_owner_lease(kind: str, entity_id: str) -> None:
    key = (kind, entity_id)
    with _owner_lease_lock:
        task = _owner_lease_tasks.pop(key, None)
    if task is not None:
        task.cancel()


async def route_control_message(kind: str, entity_id: str, message: Dict[str, Any]) -> bool:
    bus = await get_realtime_bus()
    worker_id = await bus.get_owner(kind, entity_id)
    if not worker_id:
        return False
    if worker_id == _worker_id:
        return _handle_control_message(message)
    request_id = uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    waiter: asyncio.Future[bool] = loop.create_future()
    _control_ack_waiters[request_id] = waiter
    routed_message = dict(message)
    routed_message["request_id"] = request_id
    routed_message["reply_worker_id"] = _worker_id
    try:
        await bus.send_control(worker_id, routed_message)
        return bool(await asyncio.wait_for(waiter, timeout=2.0))
    except asyncio.TimeoutError:
        return False
    finally:
        _control_ack_waiters.pop(request_id, None)
        if not waiter.done():
            waiter.cancel()


async def _control_consumer() -> None:
    subscription: Optional[EventSubscription] = None
    try:
        bus = await get_realtime_bus()
        subscription = await bus.subscribe_controls(_worker_id)
        while True:
            try:
                message = await subscription.get(timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if not isinstance(message, dict):
                continue
            kind = str(message.get("type") or "").strip().lower()
            if kind == "control.ack":
                request_id = str(message.get("request_id") or "").strip()
                waiter = _control_ack_waiters.get(request_id)
                if waiter is not None and not waiter.done():
                    waiter.set_result(bool(message.get("accepted")))
                continue

            accepted = _handle_control_message(message)
            reply_worker_id = str(message.get("reply_worker_id") or "").strip()
            request_id = str(message.get("request_id") or "").strip()
            if reply_worker_id and request_id:
                await bus.send_control(
                    reply_worker_id,
                    {
                        "type": "control.ack",
                        "request_id": request_id,
                        "accepted": accepted,
                    },
                )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Realtime control consumer stopped: %s", exc)
    finally:
        if subscription is not None:
            await subscription.close()


def _handle_control_message(message: Dict[str, Any]) -> bool:
    from app.services import chat_run_hub
    from app.services.plans.decomposition_jobs import plan_decomposition_jobs

    kind = str(message.get("type") or "").strip().lower()
    if kind == "chat_run.cancel":
        run_id = str(message.get("run_id") or "").strip()
        if run_id:
            chat_run_hub.request_cancel(run_id)
            return True
        return False
    if kind == "chat_run.steer":
        run_id = str(message.get("run_id") or "").strip()
        content = str(message.get("message") or "").strip()
        if run_id and content:
            return chat_run_hub.push_steer_message(run_id, content)
        return False
    if kind == "job.control":
        job_id = str(message.get("job_id") or "").strip()
        action = str(message.get("action") or "").strip()
        if job_id and action:
            return plan_decomposition_jobs.control_runtime(job_id, action)
        return False
    return False
