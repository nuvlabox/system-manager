#!/usr/local/bin/python3.7
# -*- coding: utf-8 -*-

""" Common set of managament methods to be used by
 the different system manager classes """

import docker
import requests


def cleanup(containers=None, exclude=None):
    """
    Cleans up all the NuvlaBox Engine containers gracefully

    :param containers: list of container objects
    :param exclude: ID to exclude
    :return:
    """

    if containers and isinstance(containers, list):

        for cont in containers:
            if exclude and exclude == cont.id:
                pass

            docker.from_env().api.stop(cont.id, timeout=5)


def change_operational_status(status):
    """ Requests the agent to change the operational status """

    url = "http://agent:5000/api/status?value={}".format(status)
    requests.get(url)