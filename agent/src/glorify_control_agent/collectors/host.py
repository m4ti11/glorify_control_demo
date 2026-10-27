import logging
import math
import os
import socket
import time

import psutil

log = logging.getLogger(__name__)
MIB = 1024**2
GIB = 1024**3


def collect(mounts, cpu):
    memory = psutil.virtual_memory()
    disks = []
    for mount in mounts:
        try:
            disk = psutil.disk_usage(mount)
            disks.append(
                {
                    "path": mount,
                    "total_gb": disk.total / GIB,
                    "used_gb": disk.used / GIB,
                    "free_gb": disk.free / GIB,
                    "percent": disk.percent,
                }
            )
        except (OSError, psutil.Error):
            log.warning("disk_collection_failed")
    if not disks or disks[0]["path"] != mounts[0]:
        raise RuntimeError("Primary disk unavailable")
    primary = {key: value for key, value in disks[0].items() if key != "free_gb"}
    metrics = {
        "hostname": socket.gethostname(),
        "uptime_seconds": max(0, int(time.time() - psutil.boot_time())),
        "cpu_percent": cpu.percent,
        "memory": {
            "total_mb": max(1, math.ceil(memory.total / MIB)),
            "used_mb": memory.used / MIB,
            "percent": memory.percent,
        },
        "disk": primary,
    }
    extras = {"disks": disks, "memory_available_mb": memory.available / MIB, "cpu": cpu.metadata}
    try:
        extras["load_average"] = list(os.getloadavg())
    except OSError:
        extras["load_average"] = None
    try:
        net = psutil.net_io_counters()
        extras["network"] = (
            {
                key: getattr(net, key)
                for key in ("bytes_sent", "bytes_recv", "errin", "errout", "dropin", "dropout")
            }
            if net
            else None
        )
    except (OSError, psutil.Error):
        extras["network"] = None
    return metrics, extras
