"""bioagent 的 Consul 服务注册/注销。

设计原则（与平台约定一致）：
1. 默认关闭：CONSUL_ENABLED 未设置时本模块不做任何事，独立启动行为完全不变。
2. 失败降级：Consul 连不上只记 warning，服务照常对外，由后台线程持续重试，
   Consul 恢复后自动补上注册。
3. 成对清理：注册与注销都由 FastAPI lifespan 驱动，进程退出自动注销，
   不会在 Consul 里残留死节点。

健康检查采用 Consul 主动轮询 HTTP 端点（同 tgrpc 的做法），
服务进程自身不需要维护心跳线程。

环境变量（可在 bioagent/.env 中配置：run.py 入口会自动加载；直接裸跑
uvicorn app.main:app 时 .env 不会被读取，CONSUL_ENABLED 将保持默认关闭）：
- CONSUL_ENABLED       是否开启注册（默认关）
- CONSUL_URL           Consul 地址（默认 http://127.0.0.1:8500）
- AGENT_SERVICE_NAME   注册的服务名（默认 agent.biomedical.main）
- AGENT_INSTANCE_ID    服务实例 ID（默认自动生成，多实例部署时需显式区分）
- AGENT_SERVICE_ADDRESS  对外宣告的服务地址（默认 127.0.0.1，容器部署需改成
                         Consul 可回连的地址，如宿主机 IP）
- AGENT_SERVICE_PORT   对外宣告的服务端口（默认取 BACKEND_PORT，再默认 9000）
- AGENT_HEALTH_CHECK_URL 健康检查完整 URL（默认 http://{address}:{port}/health；
                         Consul 跑在容器里时须用容器可回连的地址，如
                         http://host.docker.internal:9000/health）
- AGENT_CHECK_INTERVAL 健康检查间隔（默认 10s）
- AGENT_DEREGISTER_AFTER 健康检查连续失败多久后由 Consul 自动摘除（默认 60s）
"""

import logging
import os
import socket
import threading

import requests

logger = logging.getLogger("app.consul_registration")

_DEFAULT_CONSUL_URL = "http://127.0.0.1:8500"
_DEFAULT_SERVICE_NAME = "agent.biomedical.main"
# 注册失败后的重试间隔（秒）。只在「还没注册成功」期间循环，
# 成功后线程即退出，长期保活交给 Consul 的 HTTP 健康检查
_RETRY_INTERVAL_SEC = 15.0
_HTTP_TIMEOUT_SEC = 5.0


def _env_bool(name: str, default: bool = False) -> bool:
    """读取布尔环境变量，兼容 1/true/yes/on 等写法。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class ConsulRegistration:
    """封装一次进程生命周期的注册状态与后台重试线程。"""

    def __init__(self) -> None:
        self.enabled = _env_bool("CONSUL_ENABLED", False)
        self.consul_url = os.getenv("CONSUL_URL", _DEFAULT_CONSUL_URL).rstrip("/")
        self.service_name = os.getenv("AGENT_SERVICE_NAME", _DEFAULT_SERVICE_NAME)
        # 实例 ID 默认「服务名-主机名-端口」：单实例全局唯一，多实例不冲突
        self.service_id = os.getenv("AGENT_INSTANCE_ID") or (
            f"{self.service_name}-{socket.gethostname()}-{self._service_port()}"
        )
        self.service_address = os.getenv("AGENT_SERVICE_ADDRESS", "127.0.0.1")
        self.service_port = self._service_port()
        self.health_url = os.getenv(
            "AGENT_HEALTH_CHECK_URL",
            f"http://{self.service_address}:{self.service_port}/health",
        )
        self.check_interval = os.getenv("AGENT_CHECK_INTERVAL", "10s")
        self.deregister_after = os.getenv("AGENT_DEREGISTER_AFTER", "60s")
        self._registered = False
        self._stop_event = threading.Event()
        # 注册标志位的读写锁：保证「退出注销」与「后台线程恰好注册成功」
        # 不会交错，杜绝注销完成后又被补注册出死节点的竞态
        self._flag_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def _service_port(self) -> int:
        # 端口默认跟随 start_backend.sh 的 BACKEND_PORT，避免两处配置漂移
        return int(os.getenv("AGENT_SERVICE_PORT", os.getenv("BACKEND_PORT", "9000")))

    def _register_once(self) -> bool:
        """向 Consul 发起一次注册，成功返回 True。

        用 PUT /v1/agent/service/register 注册本 agent 节点上的服务，
        健康检查由 Consul 按 interval 主动轮询 health_url；
        连续失败超过 deregister_after 后 Consul 会自动摘除该实例，
        防止进程被强杀时在 Consul 里留下死节点。
        """
        payload = {
            "ID": self.service_id,
            "Name": self.service_name,
            "Tags": ["bioagent"],
            "Address": self.service_address,
            "Port": self.service_port,
            "Check": {
                "HTTP": self.health_url,
                "Interval": self.check_interval,
                "Timeout": "3s",
                "DeregisterCriticalServiceAfter": self.deregister_after,
            },
        }
        try:
            resp = requests.put(
                f"{self.consul_url}/v1/agent/service/register",
                json=payload,
                timeout=_HTTP_TIMEOUT_SEC,
            )
        except Exception as exc:
            logger.warning("Consul 注册请求失败（服务不受影响，将继续重试）: %s", exc)
            return False
        if resp.status_code == 200:
            with self._flag_lock:
                if self._stop_event.is_set():
                    # stop 已发起：本次注册立即作废，由 stop 的注销兜底清理
                    return False
                self._registered = True
            logger.info(
                "已注册到 Consul: id=%s name=%s health=%s",
                self.service_id,
                self.service_name,
                self.health_url,
            )
            return True
        logger.warning(
            "Consul 注册被拒绝 status=%s body=%s（服务不受影响，将继续重试）",
            resp.status_code,
            resp.text[:200],
        )
        return False

    def _retry_loop(self) -> None:
        """后台重试线程：注册成功前按固定间隔重试，成功后退出。"""
        while not self._stop_event.is_set():
            if self._register_once():
                return
            self._stop_event.wait(_RETRY_INTERVAL_SEC)

    def start(self) -> None:
        """lifespan 启动阶段调用；未开启开关时是 no-op，不影响独立启动。"""
        if not self.enabled:
            logger.debug("CONSUL_ENABLED 未开启，跳过 Consul 注册")
            return
        # 重入保护：线程存活期间重复 start 会覆盖 _thread 导致旧线程失控
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._retry_loop, name="consul-registration", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """lifespan 关闭阶段调用：停掉重试线程并尽力注销，异常一律吞掉。"""
        self._stop_event.set()
        if self._thread is not None:
            # join 超时也不放弃：下方注销持同一把锁，会等线程里的注册收尾，
            # 保证「注销」永远发生在任何一次「注册」之后，不留死节点
            self._thread.join(timeout=_HTTP_TIMEOUT_SEC)
            self._thread = None
        with self._flag_lock:
            if not self._registered:
                return
            self._registered = False
            try:
                resp = requests.put(
                    f"{self.consul_url}/v1/agent/service/deregister/{self.service_id}",
                    timeout=_HTTP_TIMEOUT_SEC,
                )
                if resp.status_code == 200:
                    logger.info("已从 Consul 注销: %s", self.service_id)
                else:
                    logger.warning("Consul 注销失败 status=%s", resp.status_code)
            except Exception as exc:
                # 进程退出路径上的注销失败不值得阻断关闭流程，
                # 即使残留也会被 Consul 的 DeregisterCriticalServiceAfter 兜底摘除
                logger.warning("Consul 注销异常: %s", exc)


_registration: ConsulRegistration | None = None


def start_consul_registration() -> None:
    """进程级入口：创建注册器并启动（重复调用安全）。

    任何异常都在这里吞掉并降级为 warning：注册失败绝不能阻塞服务启动，
    这是本模块对 lifespan 的承诺。
    """
    global _registration
    try:
        if _registration is None:
            _registration = ConsulRegistration()
        _registration.start()
    except Exception as exc:
        logger.warning("Consul 注册初始化失败（不影响服务启动）: %s", exc)


def stop_consul_registration() -> None:
    """进程级入口：停止重试并注销（未初始化时安全跳过）。"""
    if _registration is not None:
        try:
            _registration.stop()
        except Exception as exc:
            logger.warning("Consul 注销收尾异常: %s", exc)
