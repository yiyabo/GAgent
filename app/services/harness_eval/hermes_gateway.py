"""Finite OpenAI-compatible relay for observed Hermes comparison requests.

The relay holds the upstream credential in memory. It never records prompts,
responses, headers or credentials, and does not rewrite inference requests.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from uuid import uuid4


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ObservedGateway:
    def __init__(self, base_url, api_key, model, *, attempt_limit, deadline, journal=None, event_hook=None, max_tokens=4096, cleanup_deadline=None):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError("expected a credential-free OpenAI-compatible base URL")
        self.base_url = base_url.rstrip("/")
        self._key = api_key
        self.model = model
        self.attempt_limit = attempt_limit
        self.max_tokens = max_tokens
        self.deadline = deadline
        self.cleanup_deadline = cleanup_deadline if cleanup_deadline is not None else deadline
        self.journal = journal
        self.event_hook = event_hook
        self.observer_error = None
        self.missing_usage = False
        self.events = []
        self._lock = threading.Lock()
        self._server = None
        self._thread = None
        self._active = set()
        self._handlers = {}
        self._closing = False
        self._observer_closed = False
        self.handler_cleanup_verified = False

    def __enter__(self):
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                relay._forward(self)

            def do_GET(self):
                if self.path != "/v1/models":
                    return relay._error(self, 404, "unsupported_endpoint")
                relay._forward(self, models=True)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._server.server_port}/v1"
        return self

    def __exit__(self, *_):
        with self._lock:
            self._closing = True
        self._server.shutdown()
        with self._lock:
            active = list(self._active)
            handlers = dict(self._handlers)
        for response in active:
            # Interrupt a stalled read before close() takes its reader lock;
            # the response handler owns the eventual close.
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        for handler in handlers.values():
            try:
                handler.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for thread in handlers:
            thread.join(timeout=max(0, self.cleanup_deadline - time.monotonic()))
        with self._lock:
            self.handler_cleanup_verified = not self._handlers
            responded = {e["attempt_no"] for e in self.events if e["kind"] == "response"}
            missing = [dict(e) for e in self.events if e["kind"] == "attempt" and e["attempt_no"] not in responded]
        # A handler that could not settle is unknown spend, not zero. Keep any
        # later provider receipt in the journal for recovery, but never call the
        # already-finalized campaign observer after exiting this context.
        for event in missing:
            self._record({"kind": "response", "attempt_no": event["attempt_no"], "logical_call_id": event["logical_call_id"],
                          "usage": None, "stream_complete": False, "cleanup_deadline_exceeded": True})
        with self._lock:
            self._observer_closed = True
        self._server.server_close()
        self._thread.join(timeout=2)
        self._key = None

    @staticmethod
    def _error(handler, code, reason):
        body = json.dumps({"error": {"message": reason, "type": "evaluation_boundary"}}).encode()
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(body)

    def _record(self, event):
        event = dict(event)
        if event.get("kind") == "response":
            event["response_complete"] = True
            if not self._valid_usage(event.get("usage")):
                with self._lock:
                    self.missing_usage = True
        with self._lock:
            late = self._observer_closed
        if late:
            event["late_after_cleanup_deadline"] = True
        if self.event_hook and not late:
            try:
                receipt = dict(event)
                if receipt.get("kind") == "response":
                    receipt.update(kind="attempt", usage_source="provider" if receipt.get("usage") else "missing")
                self.event_hook(receipt)
            except Exception as exc:
                self.observer_error = type(exc).__name__
        with self._lock:
            self.events.append(dict(event))
            if self.journal:
                with self.journal.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())

    def _reserve(self, payload):
        with self._lock:
            if self.observer_error:
                return None, "observation_failed"
            if self._closing:
                return None, "evaluation_ending"
            if self.missing_usage:
                return None, "missing_usage_before_next_call"
            count = sum(e.get("kind") == "attempt" for e in self.events)
            if count >= self.attempt_limit:
                return None, "provider_attempt_limit"
            if time.monotonic() >= self.deadline:
                return None, "run_deadline_exceeded"
            if payload.get("model") != self.model:
                return None, "model_route_changed"
            limits = [payload[key] for key in ("max_tokens", "max_completion_tokens", "max_output_tokens") if key in payload]
            if not limits or any(isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self.max_tokens for limit in limits):
                return None, "output_token_limit_missing_or_exceeded"
            output_limit = max(limits)
            event = {"kind": "attempt", "attempt_no": count + 1, "logical_call_id": "hermes-" + uuid4().hex, "model": self.model,
                     "request_max_tokens": output_limit,
                     "messages_count": len(payload.get("messages") or []),
                     "message_chars": len(json.dumps(payload.get("messages") or [], ensure_ascii=False)),
                     "tool_schema_chars": len(json.dumps(payload.get("tools") or [], ensure_ascii=False)),
                     "request_options": {key: payload[key] for key in ("temperature", "top_p", "reasoning_effort", "enable_thinking") if key in payload},
                     "stream": bool(payload.get("stream")), "started_at": time.time()}
            if self.event_hook:
                try:
                    self.event_hook(dict(event))
                except Exception as exc:
                    self.observer_error = type(exc).__name__
                    return None, "campaign_attempt_limit_or_observation_failed"
            self.events.append(event)
            if self.journal:
                with self.journal.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            return event, None

    @staticmethod
    def _valid_usage(usage):
        return isinstance(usage, dict) and all(isinstance(usage.get(key), int) and not isinstance(usage[key], bool) and usage[key] >= 0
                                               for key in ("prompt_tokens", "completion_tokens", "total_tokens"))

    @staticmethod
    def _observe_object(obj, result):
        if not isinstance(obj, dict):
            return
        if isinstance(obj.get("usage"), dict):
            result["usage"] = {key: obj["usage"].get(key) for key in
                               ("prompt_tokens", "completion_tokens", "total_tokens",
                                "prompt_tokens_details", "completion_tokens_details") if key in obj["usage"]}
        reasons = [c.get("finish_reason") for c in obj.get("choices", []) if isinstance(c, dict) and c.get("finish_reason")]
        if reasons:
            result["finish_reasons"] = reasons

    def _forward(self, handler, models=False):
        thread = threading.current_thread()
        with self._lock:
            self._handlers[thread] = handler
        handler.connection.settimeout(max(.1, min(120, self.deadline - time.monotonic())))
        try:
            self._forward_request(handler, models)
        finally:
            with self._lock:
                self._handlers.pop(thread, None)

    def _forward_request(self, handler, models=False):
        if not models and handler.path != "/v1/chat/completions":
            return self._error(handler, 404, "unsupported_endpoint")
        attempt = None
        body = None
        if not models:
            try:
                size = int(handler.headers.get("Content-Length", "0"))
                if not 0 < size <= 32 * 1024 * 1024:
                    raise ValueError("request_size")
                body = handler.rfile.read(size)
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("request_shape")
            except (ValueError, TypeError):
                return self._error(handler, 400, "invalid_request")
            attempt, denied = self._reserve(payload)
            if denied:
                self._record({"kind": "denied", "reason": denied})
                return self._error(handler, 429, denied)
        if time.monotonic() >= self.deadline:
            return self._error(handler, 408, "run_deadline_exceeded")
        result = {"kind": "response", "attempt_no": attempt["attempt_no"] if attempt else None,
                  "logical_call_id": attempt["logical_call_id"] if attempt else None,
                  "usage": None, "finish_reasons": [], "stream_complete": False}
        started = time.monotonic()
        response = None
        sent_headers = False
        try:
            request = Request(self.base_url + ("/models" if models else "/chat/completions"), data=body,
                              headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"},
                              method="GET" if models else "POST")
            try:
                response = build_opener(_NoRedirect).open(request, timeout=max(.1, min(120, self.deadline - time.monotonic())))
            except HTTPError as exc:
                response = exc
            with self._lock:
                self._active.add(response)
            result["http_status"] = response.status
            content_type = response.headers.get("Content-Type", "application/json")
            handler.send_response(response.status)
            handler.send_header("Content-Type", content_type)
            handler.send_header("Connection", "close")
            handler.end_headers()
            sent_headers = True
            if "text/event-stream" in content_type:
                while time.monotonic() < self.deadline:
                    line = response.readline()
                    if not line:
                        break
                    if line.startswith(b"data:"):
                        data = line[5:].strip()
                        if data == b"[DONE]":
                            result["stream_complete"] = True
                        else:
                            try:
                                self._observe_object(json.loads(data), result)
                            except ValueError:
                                result["malformed_stream_event"] = True
                    handler.wfile.write(line)
                    handler.wfile.flush()
            else:
                content = response.read()
                try:
                    self._observe_object(json.loads(content), result)
                    result["stream_complete"] = True
                except ValueError:
                    result["malformed_response"] = True
                handler.wfile.write(content)
        except Exception as exc:
            result["error_type"] = type(exc).__name__
            if not sent_headers:
                try:
                    self._error(handler, 502, "upstream_transport_error")
                except OSError:
                    pass
        finally:
            if response:
                response.close()
                with self._lock:
                    self._active.discard(response)
            handler.close_connection = True
            result["duration_seconds"] = round(time.monotonic() - started, 3)
            if attempt:
                self._record(result)

    def accounting(self):
        attempts = [e for e in self.events if e["kind"] == "attempt"]
        responses = {e["attempt_no"]: e for e in self.events if e["kind"] == "response"}
        verified = [responses.get(e["attempt_no"], {}).get("usage") for e in attempts]
        valid = [self._valid_usage(u) for u in verified]
        complete = bool(attempts) and all(valid)
        sums = {key: sum(u.get(key, 0) for u in verified if isinstance(u, dict) and isinstance(u.get(key), int))
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        return {"provider_attempts": len(attempts), "usage_source": "provider" if complete else "missing",
                **{key: value if complete else None for key, value in sums.items()},
                "known_usage": sums, "usage_missing_attempts": sum(not value for value in valid)}
