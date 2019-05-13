#!/usr/local/bin/python3.7
# -*- coding: utf-8 -*-

"""NuvlaBox System Manager service

This service makes sure the NuvlaBox engine can be
installed in the device and supervises all internal
components of the NuvlaBox

Arguments:

"""

import logging
import sys
from system_manager.common import utils
from system_manager.Requirements import SystemRequirements, SoftwareRequirements
from system_manager.Supervise import Supervise


__copyright__ = "Copyright (C) 2019 SixSq"
__email__ = "support@sixsq.com"

data_volume = "/srv/nuvlabox/shared"
log_filename = "system-manager.log"


def set_logger(log_level, log_file):
    """ Configures logging """

    logging.basicConfig(level=log_level)
    root_logger = logging.getLogger()

    file_handler = logging.FileHandler(log_file)
    root_logger.addHandler(file_handler)

    # stdout_handler = logging.StreamHandler(sys.stdout)
    # root_logger.addHandler(stdout_handler)

    return root_logger


if __name__ == "__main__":
    """ Main """

    logging = set_logger(logging.INFO, "{}/{}".format(data_volume, log_filename))

    system_requirements = SystemRequirements()
    software_requirements = SoftwareRequirements()
    supervisor = Supervise()

    if not software_requirements.check_docker_requirements() or not system_requirements.check_all_hw_requirements():
        logging.error("System does not meet the minimum requirements! Stopping")
        utils.cleanup(supervisor.list_internal_containers(), supervisor.docker_id)
        sys.exit(1)
    else:
        while True:
            pass




