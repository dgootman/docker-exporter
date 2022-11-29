from typing import Callable
from wsgiref.simple_server import make_server

import docker
from tqdm.contrib.concurrent import thread_map
from prometheus_client import make_wsgi_app
from prometheus_client.core import REGISTRY, GaugeMetricFamily


docker_client = docker.from_env()


class CustomCollector(object):
    def collect(self):
        stats = list(
            thread_map(
                lambda c: c.stats(stream=False),
                docker_client.containers.list(),
            )
        )

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
                    for io in s["blkio_stats"]["io_service_bytes_recursive"]
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
                    for io in s["blkio_stats"]["io_service_bytes_recursive"]
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
httpd = make_server("", 8080, app)

print("Started server: http://localhost:8080")
httpd.serve_forever()
