#!/usr/local/bin/python3.7
# -*- coding: utf-8 -*-

""" Contains the supervising class for all NuvlaBox Engine components """

import docker
import logging
import json
import time
import os
import glob
import OpenSSL
import requests
from datetime import datetime
from system_manager.common import utils


class Supervise():
    """ The Supervise class contains all the methods and
    definitions for making sure the NuvlaBox Engine is running smoothly,
    including all methods for dealing with system disruptions and
    graceful shutdowns
    """

    def __init__(self):
        """ Constructs the Supervise object """

        self.docker_client = docker.from_env()
        self.log = logging.getLogger(__name__)
        self.system_usages = {}

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

    def get_nuvlabox_peripherals(self):
        """ Reads the list of peripherals discovered by the other NuvlaBox microservices,
        via the shared volume folder

        :returns list of peripherals [{...}, {...}] with the original data schema (see Nuvla nuvlabox-peripherals)
        """

        peripherals = []
        try:
            peripheral_files = glob.iglob(utils.nuvlabox_peripherals_folder + '**/**', recursive=True)
        except FileNotFoundError:
            return peripherals

        for per_file_path in peripheral_files:
            if os.path.isdir(per_file_path):
                continue
            try:
                with open(per_file_path) as p:
                    peripheral_content = json.loads(p.read())
            except FileNotFoundError:
                logging.warning("Cannot read peripheral {}".format(per_file_path))
                continue

            peripherals.append(peripheral_content)

        return peripherals

    def get_internal_logs_html(self, tail=30, since=None):
        """ Get the logs for all NuvlaBox containers

        :returns list of log generators
        :returns timestamp for when the logs were fetched
        """

        nb_containers = utils.list_internal_containers()
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

        errors = []
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

                    cpu_delta = cpu_total - previous_cpu
                    system_delta = cpu_system - previous_system

                    if system_delta > 0.0 and online_cpus > -1:
                        cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0

                    previous_system = cpu_system
                    previous_cpu = cpu_total
                except (IndexError, KeyError, ValueError, ZeroDivisionError) as e:
                    self.log.debug(f"Cannot get CPU stats for container {container.name}: {str(e)}. Moving on")
                    cpu_percent = 0.0
                    error_name = f'{container.name}:cpu:{str(e)}'
                    if error_name not in errors:
                        errors.append(error_name)

                # generate stats at least twice
                x += 1
                if x >= 2:
                    try:
                        mem_usage = float(container_stats["memory_stats"]["usage"] / 1024 / 1024)
                        mem_limit = float(container_stats["memory_stats"]["limit"] / 1024 / 1024)
                        if round(mem_limit, 2) == 0.00:
                            mem_percent = 0.00
                        else:
                            mem_percent = round(float(mem_usage / mem_limit) * 100, 2)
                    except (IndexError, KeyError, ValueError) as e:
                        self.log.debug(f"Cannot get Mem stats for container {container.name}: {str(e)}. Moving on")
                        mem_percent = mem_usage = mem_limit = 0.00
                        error_name = f'{container.name}:mem:{str(e)}'
                        if error_name not in errors:
                            errors.append(error_name)

                    if "networks" in container_stats:
                        net_in = sum(container_stats["networks"][iface]["rx_bytes"] for iface in container_stats["networks"]) / 1000 / 1000
                        net_out = sum(container_stats["networks"][iface]["tx_bytes"] for iface in container_stats["networks"]) / 1000 / 1000

                    try:
                        blk_in = float(container_stats.get("blkio_stats", {}).get("io_service_bytes_recursive", [{"value": 0}])[0]["value"] / 1000 / 1000)
                    except Exception as e:
                        self.log.debug(f"Cannot get Block stats for container {container.name}: {str(e)}. Moving on")
                        blk_in = 0.0
                        error_name = f'{container.name}:block-in:{str(e)}'
                        if error_name not in errors:
                            errors.append(error_name)
                    try:
                        blk_out = float(container_stats.get("blkio_stats", {}).get("io_service_bytes_recursive", [0, {"value": 0}])[1]["value"] / 1000 / 1000)
                    except Exception as e:
                        self.log.debug(f"Cannot get Block stats for container {container.name}: {str(e)}. Moving on")
                        blk_out = 0.0
                        error_name = f'{container.name}:block-out:{str(e)}'
                        if error_name not in errors:
                            errors.append(error_name)

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

        if errors:
            self.log.warning(f'Failed to get some container stats. List (container:metric:error): {", ".join(errors)}')

        stats += ' </tbody>' \
                 '</table>'
        self.printer(stats, utils.docker_stats_html_file)

    def is_cert_rotation_needed(self):
        """ Checks whether the Docker and NB API certs are about to expire """

        # certificates to be checked for expiration dates:
        check_expiry_date_on = ["ca.pem", "server-cert.pem", "cert.pem"]

        # if the TLS sync file does not exist, then the compute-api is going to generate the certs by itself, by default
        if not os.path.isfile(utils.tls_sync_file):
            return False

        for file in check_expiry_date_on:
            file_path = f"{utils.data_volume}/{file}"

            if os.path.isfile(file_path):
                with open(file_path) as fp:
                    content = fp.read()

                cert_obj = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, content)

                end_date = cert_obj.get_notAfter().decode()
                formatted_end_date = datetime(int(end_date[0:4]),
                                              int(end_date[4:6]),
                                              int(end_date[6:8]))

                days_left = formatted_end_date - datetime.now()
                # if expiring in less than d days, rotate all
                d = 5
                if days_left.days < d:
                    self.log.warning(f"{file_path} is expiring in less than {d} days. Requesting rotation of all certs")
                    return True

        return False

    def request_rotate_certificates(self):
        """ Deletes the existing .tls sync file from the shared volume and restarts the compute-api container

        This restart will force the regeneration of the certificates and consequent recommissioning """

        compute_api_container = "compute-api"

        if os.path.isfile(utils.tls_sync_file):
            os.remove(utils.tls_sync_file)
            self.log.info(f"Removed {utils.tls_sync_file}. Restarting {compute_api_container} container")
            try:
                self.docker_client.api.restart(compute_api_container, timeout=30)
            except docker.errors.NotFound:
                self.log.exception(f"Container {compute_api_container} is not running. Nothing to do...")

    def keep_datagateway_up(self):
        """ Restarts the datagateway if it is down

        :return:
        """

        container_name = 'datagateway'

        degraded = 'DEGRADED'
        try:
            datagateway_container = self.docker_client.containers.get(container_name)
        except docker.errors.NotFound:
            self.log.warning(f'{container_name} container is not running. Setting operational status to {degraded}')
            utils.set_operational_status(degraded)
            return
        except:
            # really nothing to do
            return

        if datagateway_container.status.lower() not in ["running", "paused"]:
            self.log.warning(f'{container_name} is down and not restarting on its own. Forcing the restart...')
            try:
                datagateway_container.start()
            except:
                self.log.exception(f'Unable to force restart {container_name}. Setting operational status to {degraded}')
                utils.set_operational_status(degraded)

            utils.set_operational_status('OPERATIONAL')

    def keep_datagateway_containers_up(self):
        """ Restarts the datagateway containers, if any. These are identified by their labels

        :return:
        """

        container_label = 'nuvlabox.data-source-container=True'

        datagateway_containers = self.docker_client.containers.list(all=True, filters={'label': container_label})

        if datagateway_containers:
            peripherals = self.get_nuvlabox_peripherals()
        else:
            return

        peripheral_ids = list(map(lambda x: x.get("id"), peripherals))

        for dg_container in datagateway_containers:
            id = f"nuvlabox-peripheral/{dg_container.name}"
            if id not in peripheral_ids:
                # then it means the peripheral is gone, and the DG container was not removed
                self.log.warning(f"Found old DG container {dg_container.name}. Trying to disable it")
                try:
                    r = requests.post("https://management-api:5001/api/data-source-mjpg/disable",
                                      verify=False,
                                      cert=(utils.cert_file, utils.key_file),
                                      json={"id": id})
                    r.raise_for_status()
                except:
                    # force disable manual
                    self.log.exception(f"Could not disable DG container {dg_container.name} via the management-api. Force deleting it...")
                    try:
                        dg_container.remove(force=True)
                    except Exception as e:
                        self.log.error(f"Unable to cleanup old DG container {dg_container.name}: {str(e)}")

                continue

            if dg_container.status.lower() not in ["running", "paused"]:
                self.log.warning(f'The data-gateway container {dg_container.name} is down. Forcing its restart...')

            try:
                dg_container.start()
            except Exception as e:
                self.log.exception(f'Unable to force restart {dg_container.name}. Reason: {str(e)}')
