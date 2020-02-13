#!/usr/local/bin/python3.7
# -*- coding: utf-8 -*-

""" Contains the supervising class for all NuvlaBox Engine components """

import docker
import logging
import json
import time
from datetime import datetime
from system_manager.common import utils
from threading import Thread


class Supervise(Thread):
    """ The Supervise class contains all the methods and
    definitions for making sure the NuvlaBox Engine is running smoothly,
    including all methods for dealing with system disruptions and
    graceful shutdowns
    """

    def __init__(self):
        """ Constructs the Supervise object """

        self.docker_client = docker.from_env()
        self.base_label = "nuvlabox.component=True"
        self.log = logging.getLogger("app")
        self.system_usages = {}

        with open("/proc/self/cgroup", 'r') as f:
            self.docker_id = f.readlines()[0].replace('\n', '').split("/")[-1]

        Thread.__init__(self)
        self.daemon = True
        self.start()

    def list_internal_containers(self):
        """ Gets all the containers that compose the NuvlaBox Engine """

        return self.docker_client.containers.list(filters={"label": self.base_label})

    @staticmethod
    def printer(content, file):
        """ Pretty prints to template file """

        with open("{}/{}".format(utils.html_templates, file), 'w') as p:
            p.write("{}".format(content))

    @staticmethod
    def reader(file):
        """ Reads template file

        :returns file content as a string
        """

        with open("{}/{}".format(utils.html_templates, file)) as r:
            return r.read()

    def get_nuvlabox_status(self):
        """ Re-uses the consumption metrics from NuvlaBox Agent """

        try:
            with open(utils.nuvlabox_status_file) as nbsf:
                usages = json.loads(nbsf.read())
        except FileNotFoundError:
            self.log.warning("NuvlaBox status metrics file not found locally...wait for Agent to create it")
            usages = {}
        except:
            self.log.exception("Unknown issues while retrieving NuvlaBox status metrics")
            usages = self.system_usages

        # update in-mem copy of usages
        self.system_usages = usages

        return usages

    def get_docker_disk_usage(self):
        """ Runs docker system df and gets disk usage """

        return round(float(self.docker_client.df()["LayersSize"] / 1000 / 1000 / 1000), 2)

    def get_docker_info(self):
        """ Gets everything from the Docker client info """

        return self.docker_client.info()

    def get_internal_logs_html(self, tail=30, since=None):
        """ Get the logs for all NuvlaBox containers

        :returns list of log generators
        :returns timestamp for when the logs were fetched
        """

        nb_containers = self.list_internal_containers()
        logs = ''
        for container in nb_containers:
            container_log = self.docker_client.api.logs(container.id,
                                                        timestamps=True,
                                                        tail=tail,
                                                        since=since).decode('utf-8')

            if container_log:
                log_id = '<b style="color: #{};">{} |</b> '.format(container.id[:6], container.name)
                logs += '{} {}'.format(log_id,
                                       '<br/>{}'.format(log_id).join(container_log.splitlines()))
                logs += '<br/>'
        return logs, int(time.time())

    def write_docker_stats_table_html(self):
        """ Run docker stats """

        stats = '<table class="table table-striped table-hover mt-5 mr-auto">' \
                ' <caption>Docker Stats, last update: {} UTC</caption>' \
                ' <thead class="bg-secondary text-light">' \
                '  <tr>' \
                '    <th scope="col">CONTAINER ID</th>' \
                '    <th scope="col">NAME</th>' \
                '    <th scope="col">CPU %</th>' \
                '    <th scope="col">MEM USAGE/LIMIT</th>' \
                '    <th scope="col">MEM %</th>' \
                '    <th scope="col">NET I/O</th>' \
                '    <th scope="col">BLOCK I/O</th>' \
                '    <th scope="col">STATUS</th>' \
                '    <th scope="col">RESTARTED</th>' \
                '  </tr>' \
                ' </thead>' \
                ' <tbody>'.format(datetime.utcnow())

        for container in self.docker_client.containers.list():
            previous_cpu = previous_system = cpu_percent = mem_percent = mem_usage = mem_limit = net_in = net_out = blk_in = blk_out = 0.0
            restart_count = 0
            container_status = "unknown"
            x = 0
            # TODO: this should be executed in parallel, one thread per generator
            for container_stats in self.docker_client.api.stats(container.id, stream=True, decode=True):
                cpu_percent = 0.0

                try:
                    cpu_total = float(container_stats["cpu_stats"]["cpu_usage"]["total_usage"])
                    cpu_system = float(container_stats["cpu_stats"]["system_cpu_usage"])
                    online_cpus = container_stats["cpu_stats"] \
                        .get("online_cpus", len(container_stats["cpu_stats"]["cpu_usage"].get("percpu_usage", -1)))
                except (IndexError, KeyError, ValueError):
                    self.log.warning("Cannot get CPU stats for container {}. Moving on".format(container.name))
                    break

                cpu_delta = cpu_total - previous_cpu
                system_delta = cpu_system - previous_system

                if system_delta > 0.0 and online_cpus > -1:
                    cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0

                previous_system = cpu_system
                previous_cpu = cpu_total

                # generate stats at least twice
                x += 1
                if x >= 2:
                    try:
                        mem_usage = float(container_stats["memory_stats"]["usage"] / 1024 / 1024)
                        mem_limit = float(container_stats["memory_stats"]["limit"] / 1024 / 1024)
                    except (IndexError, KeyError, ValueError):
                        self.log.warning("Cannot get Mem stats for container {}. Moving on".format(container.name))
                        break

                    if round(mem_limit, 2) == 0.00:
                        mem_percent = 0.00
                    else:
                        mem_percent = round(float(mem_usage / mem_limit) * 100, 2)

                    if "networks" in container_stats:
                        net_in = sum(container_stats["networks"][iface]["rx_bytes"] for iface in container_stats["networks"]) / 1000 / 1000
                        net_out = sum(container_stats["networks"][iface]["tx_bytes"] for iface in container_stats["networks"]) / 1000 / 1000

                    try:
                        blk_in = float(container_stats.get("blkio_stats", {}).get("io_service_bytes_recursive", [{"value": 0}])[0]["value"] / 1000 / 1000)
                    except IndexError:
                        blk_in = 0.0
                    try:
                        blk_out = float(container_stats.get("blkio_stats", {}).get("io_service_bytes_recursive", [0, {"value": 0}])[1]["value"] / 1000 / 1000)
                    except IndexError:
                        blk_out = 0.0
                    container_status = container.status
                    restart_count = int(container.attrs["RestartCount"]) if "RestartCount" in container.attrs else 0

                    stats += '<tr>' \
                             ' <th scope="row">{}</th> ' \
                             ' <td>{}</td>' \
                             ' <td>{}</td>' \
                             ' <td>{}</td>' \
                             ' <td>{}</td>' \
                             ' <td>{}</td>' \
                             ' <td>{}</td>' \
                             ' <td>{}</td>' \
                             ' <td>{}</td>' \
                             '</tr>'.format(container.id[:12],
                                            container.name[:25],
                                            "%.2f" % round(cpu_percent, 2),
                                            "%sMiB / %sGiB" % (round(mem_usage, 2), round(mem_limit / 1024, 2)),
                                            "%.2f" % mem_percent,
                                            "%sMB / %sMB" % (round(net_in, 2), round(net_out, 2)),
                                            "%sMB / %sMB" % (round(blk_in, 2), round(blk_out, 2)),
                                            container_status,
                                            restart_count)
                    # stop streaming
                    break

        stats += ' </tbody>' \
                 '</table>'
        self.printer(stats, utils.docker_stats_html_file)

    def run(self):
        """ Run the docker_stats streaming """
        while True:
            try:
                self.write_docker_stats_table_html()
            except:
                # catch all exceptions, cause if there's any problem, we simply want the thread to restart
                self.log.exception("Restarting Docker stats streamer...")
                pass
            time.sleep(2)



