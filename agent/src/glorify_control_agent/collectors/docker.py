import logging
from threading import Lock

import docker

log = logging.getLogger(__name__)


class DockerCollector:
    def __init__(self, settings):
        self.settings = settings
        self.lock = Lock()
        self.previous = {}

    def collect(self):
        with self.lock:
            return self._collect()

    def _collect(self):
        client = None
        try:
            client = docker.APIClient(
                base_url="unix://" + self.settings.docker_socket, timeout=self.settings.docker_timeout
            )
            summaries = client.containers(all=True)
            if len(summaries) > self.settings.docker_max_containers:
                log.warning("container_collection_truncated")
            containers = []
            complete = len(summaries) <= self.settings.docker_max_containers
            for summary in summaries[: self.settings.docker_max_containers]:
                try:
                    info = client.inspect_container(summary["Id"])
                    state = info["State"]
                    metrics = {
                        "name": info["Name"].lstrip("/"),
                        "image": info["Config"]["Image"],
                        "state": state["Status"],
                        "health": state.get("Health", {}).get("Status"),
                        "started_at": None
                        if state.get("StartedAt", "0001").startswith("0001")
                        else state.get("StartedAt"),
                        "restart_count": info["RestartCount"],
                        "cpu_percent": None,
                        "memory_usage_mb": None,
                        "memory_limit_mb": None,
                        "memory_percent": None,
                    }
                    if state["Running"]:
                        try:
                            stats = client.stats(summary["Id"], stream=False, one_shot=True)
                            cpu = stats.get("cpu_stats", {})
                            previous = stats.get("precpu_stats", {})
                            if not previous.get("system_cpu_usage"):
                                previous = self.previous.get(summary["Id"], {})
                            self.previous[summary["Id"]] = cpu
                            delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - previous.get(
                                "cpu_usage", {}
                            ).get("total_usage", 0)
                            system = cpu.get("system_cpu_usage", 0) - previous.get("system_cpu_usage", 0)
                            count = (
                                cpu.get("online_cpus")
                                or len(cpu.get("cpu_usage", {}).get("percpu_usage", []))
                                or 1
                            )
                            metrics["cpu_percent"] = (
                                min(100, max(0, delta / system * count * 100))
                                if system > 0 and previous.get("system_cpu_usage")
                                else None
                            )
                            mem = stats.get("memory_stats", {})
                            used = max(0, mem.get("usage", 0) - mem.get("stats", {}).get("inactive_file", 0))
                            limit = mem.get("limit", 0)
                            metrics.update(
                                memory_usage_mb=min(used, limit) / 1024**2 if limit else used / 1024**2,
                                memory_limit_mb=limit / 1024**2 if limit else None,
                                memory_percent=min(100, used / limit * 100) if limit else None,
                            )
                        except Exception:
                            metrics.update(cpu_percent=None, memory_usage_mb=None, memory_limit_mb=None, memory_percent=None)
                            log.warning("container_stats_unavailable")
                    containers.append(metrics)
                except Exception:
                    complete = False
                    log.warning("container_inspect_unavailable")
            live_ids = {summary["Id"] for summary in summaries}
            self.previous = {key: value for key, value in self.previous.items() if key in live_ids}
            return {
                "available": True,
                "running_containers": sum(c["state"] == "running" for c in containers),
                "total_containers": len(containers),
                "containers": containers,
            }, complete
        except Exception:
            return {
                "available": False,
                "running_containers": 0,
                "total_containers": 0,
                "containers": [],
            }, False
        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    log.warning("docker_client_close_failed")
