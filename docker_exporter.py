import json
import logging
import os
from concurrent.futures.thread import ThreadPoolExecutor
from pathlib import Path
from typing import Callable
from wsgiref.simple_server import make_server

import docker
from prometheus_client import make_wsgi_app
from prometheus_client.core import REGISTRY, GaugeMetricFamily
from requests.adapters import HTTPAdapter

logging.basicConfig()

MAX_POOL_SIZE = 100

host = os.environ.get("HTTP_HOST", "")
port = int(os.environ.get("HTTP_PORT", 8080))
debug = os.environ.get("DEBUG", "false").lower() in ["true", "on", "y", "yes", "1"]


logger = logging.getLogger(Path(__file__).stem)
if debug:
    logger.setLevel(logging.DEBUG)


docker_client = docker.from_env(max_pool_size=MAX_POOL_SIZE)

# Patch the default HTTPAdapter for docker to use a pool size of MAX_POOL_SIZE
# Setting max_pool_size in docker.from_env doen't take effect for HTTP connections
if (
    "http://" in docker_client.api.adapters
    and docker_client.api.adapters["http://"]._pool_maxsize < MAX_POOL_SIZE
):
    docker_client.api.mount("http://", HTTPAdapter(pool_maxsize=MAX_POOL_SIZE))


class CustomCollector(object):
    def collect(self):
        with ThreadPoolExecutor() as t:
            stats = list(
                t.map(
                    lambda c: c.stats(stream=False),
                    docker_client.containers.list(),
                )
            )

        logger.debug(f"Stats: {json.dumps(stats)}")

        def gauge_metric(
            name: str,
            documentation: str,
            supplier: Callable[[dict], float],
        ):
            g = GaugeMetricFamily(name, documentation, labels=["name"])
            for stat in stats:
                g.add_metric([stat["name"].lstrip("/")], supplier(stat))
            return g

        yield gauge_metric(
            "container_cpu_usage_total",
            "Total CPU time consumed",
            lambda s: s["cpu_stats"]["cpu_usage"]["total_usage"],
        )
        yield gauge_metric(
            "container_cpu_usage_kernel",
            "Time spent by tasks of the cgroup in kernel mode",
            lambda s: s["cpu_stats"]["cpu_usage"]["usage_in_kernelmode"],
        )
        yield gauge_metric(
            "container_cpu_usage_user",
            "Time spent by tasks of the cgroup in user mode",
            lambda s: s["cpu_stats"]["cpu_usage"]["usage_in_usermode"],
        )
        yield gauge_metric(
            "container_cpu_usage_system",
            "System Usage",
            lambda s: s["cpu_stats"]["system_cpu_usage"],
        )
        yield gauge_metric(
            "container_mem_usage",
            "Total memory usage for container",
            lambda s: s["memory_stats"]["usage"],
        )
        yield gauge_metric(
            "container_mem_limit",
            "Memory usage limit for container",
            lambda s: s["memory_stats"]["limit"],
        )
        yield gauge_metric(
            "container_io_read_total",
            "Total IO read by the container",
            lambda s: sum(
                [
                    io["value"]
                    for io in s["blkio_stats"]["io_service_bytes_recursive"] or []
                    if io["op"] == "read"
                ]
            ),
        )
        yield gauge_metric(
            "container_io_write_total",
            "Total IO written by the container",
            lambda s: sum(
                [
                    io["value"]
                    for io in s["blkio_stats"]["io_service_bytes_recursive"] or []
                    if io["op"] == "write"
                ]
            ),
        )

        network_metrics = {
            key
            for stat in stats
            for network in stat.get("networks", {}).values()
            for key in network.keys()
        }
        for network_metric in network_metrics:
            g = GaugeMetricFamily(
                f"container_net_{network_metric}",
                f"Network metric {network_metric}",
                labels=["name", "network"],
            )
            for stat in stats:
                for network_name, network in stat.get("networks", {}).items():
                    g.add_metric(
                        [stat["name"].lstrip("/"), network_name],
                        network[network_metric],
                    )
            yield g


REGISTRY.register(CustomCollector())

app = make_wsgi_app()
httpd = make_server(host, port, app)

print(f"Started server: http://localhost:{port}")
httpd.serve_forever()
