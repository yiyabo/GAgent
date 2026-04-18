from __future__ import annotations

import asyncio
import time

from app.services import realtime_bus


def test_start_owner_lease_from_worker_thread_does_not_require_thread_event_loop() -> None:
    async def _run() -> None:
        await realtime_bus.close_realtime_bus()
        await realtime_bus.init_realtime_bus()
        errors: list[Exception] = []

        try:
            def _worker() -> None:
                try:
                    realtime_bus.start_owner_lease("job", "thread-owned-job")
                except Exception as exc:  # pragma: no cover - defensive
                    errors.append(exc)

            await asyncio.to_thread(_worker)
            await asyncio.sleep(0.05)

            bus = await realtime_bus.get_realtime_bus()
            owner = await bus.get_owner("job", "thread-owned-job")
            assert not errors
            assert owner == realtime_bus.get_worker_id()
        finally:
            realtime_bus.stop_owner_lease("job", "thread-owned-job")
            await realtime_bus.close_realtime_bus()

    asyncio.run(_run())


def test_route_control_message_waits_for_remote_ack() -> None:
    async def _run() -> None:
        await realtime_bus.close_realtime_bus()
        await realtime_bus.init_realtime_bus()

        bus = await realtime_bus.get_realtime_bus()
        remote_subscription = await bus.subscribe_controls("remote-worker")
        try:
            await bus.register_owner("job", "remote-job", "remote-worker")

            async def _remote_worker() -> None:
                message = await remote_subscription.get(timeout=1.0)
                await asyncio.sleep(1.1)
                await bus.send_control(
                    realtime_bus.get_worker_id(),
                    {
                        "type": "control.ack",
                        "request_id": message["request_id"],
                        "accepted": True,
                    },
                )

            remote_task = asyncio.create_task(_remote_worker())
            started = time.monotonic()
            accepted = await realtime_bus.route_control_message(
                "job",
                "remote-job",
                {"type": "job.control", "job_id": "remote-job", "action": "pause"},
            )
            elapsed = time.monotonic() - started

            assert accepted is True
            assert elapsed < 1.8
            await remote_task
        finally:
            await remote_subscription.close()
            await realtime_bus.close_realtime_bus()

    asyncio.run(_run())
