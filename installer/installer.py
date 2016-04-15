import json
import logging
import os
import re
import shutil
import socket
import sys
import time
from collections import OrderedDict

import psutil

_log = logging.getLogger(__name__)

# Calico installation config files
INSTALLER_CONFIG_DIR = "/etc/calico/installer"
NETMODULES_INSTALL_CONFIG= INSTALLER_CONFIG_DIR + "/netmodules"
DOCKER_INSTALL_CONFIG = INSTALLER_CONFIG_DIR + "/docker"

# Docker information for a standard Docker install.
DOCKER_DAEMON_EXE_RE = re.compile("/usr/bin/docker\s+daemon\s+.*")
DOCKER_DAEMON_CONFIG = "/etc/docker/daemon.json"

# Docker information for a standard Docker install.
AGENT_EXE_RE = re.compile("/opt/mesosphere.*/bin/mesos-slave.*")
AGENT_CONFIG = "/opt/mesosphere/etc/mesos-slave-common"
AGENT_MODULES_CONFIG = "/opt/mesosphere/etc/mesos-slave-modules.json"

# Fixed address for our etcd proxy.
CLUSTER_STORE_ETCD_PROXY = "etcd://127.0.0.1:2379"

# Max time for process restarts (in seconds)
MAX_TIME_FOR_DOCKER_RESTART = 30
MAX_TIME_FOR_AGENT_RESTART = 30

# Time to wait to check that a process is stable.
PROCESS_STABILITY_TIME = 5


def ensure_dir(directory):
    """
    Ensure the specified directory exists
    :param directory:
    """
    if not os.path.exists(directory):
        os.makedirs(directory)


def find_process(exe_re):
    """
    Find the unique process specified by the executable and command line
    regexes.

    :param exe_re: Regex used to search for a particular executable in the
    process list.  The exe regex is compared against the full command line
    invocation - so executable plus arguments.
    :return: The matching process, or None if no process is found.  If
    multiple matching processes are found, this indicates an error with the
    regex, and the script will terminate.
    """
    processes = [
        p for p in psutil.process_iter()
        if exe_re.match(" ".join(p.cmdline()))
    ]

    # Multiple processes suggests our query is not correct.
    if len(processes) > 1:
        _log.error("Found multiple matching processes: %s", exe_re)
        sys.exit(1)

    return processes[0] if processes else None


def wait_for_process(exe_re, max_wait, stability_time=0):
    """
    Locate the specified process, waiting a specified max time before giving
    up.  If the process can not be found within the time limit, the script
    exits.
    :param exe_re: Regex used to search for a particular executable in the
    process list.  The exe regex is compared against the full command line
    invocation - so executable plus arguments.
    :param max_wait:  The maximum time to wait for the process to appear.
    :param stability_time:  The time to wait to check for process stability.
    :return: The matching process, or None if no process is found.  If
    multiple matching processes are found, this indicates an error with the
    regex, and the script will terminate.  If no processes are found, this
    also indicates a problem and the script terminates.
    """
    start = time.time()
    process = find_process(exe_re)
    while not process and time.time() < start + max_wait:
        time.sleep(1)
        process = find_process(exe_re)

    if not process:
        _log.error("Process not found within timeout: %s", exe_re)
        sys.exit(1)

    if stability_time:
        _log.debug("Waiting to check process stability")
        time.sleep(stability_time)
        new_process = find_process(exe_re)
        if not new_process:
            _log.error("Process terminated unexpectedly: %s", process)
            sys.exit(1)
        if process.pid != new_process.pid:
            _log.error("Process restarting unexpectedly: %s -> %s",
                       process, new_process)
            sys.exit(1)

    return process


def load_config(filename):
    """
    Load a JSON config file from disk.
    :param filename:  The filename of the config file
    :return:  A dictionary containing the JSON data.  If the file was not found
    an empty dictionary is returned.
    """
    if os.path.exists(filename):
        _log.debug("Reading config file: %s", filename)
        with open(filename) as f:
            config = json.loads(f.read())
    else:
        _log.debug("Config file does not exist: %s", filename)
        config = {}
    _log.debug("Returning config:\n%s", config)
    return config


def store_config(filename, config):
    """
    Store the supplied config as a JSON file.  This performs an atomic write
    to the config file by writing to a temporary file and then renaming the
    file.
    :param filename:  The filename
    :param config:  The config (a simple dictionary)
    """
    ensure_dir(os.path.dirname(filename))
    atomic_write(filename, json.dumps(config))


def load_property_file(filename):
    """
    Loads a file containing x=a,b,c... properties separated by newlines, and
    returns an OrderedDict where the key is x and the value is [a,b,c...]
    :param filename:
    :return:
    """
    props = OrderedDict()
    if not os.path.exists(filename):
        return props
    with open(filename) as f:
        for line in f:
            line = line.strip().split("=", 1)
            if len(line) != 2:
                continue
            props[line[0].strip()] = line[1].split(",")
    _log.debug("Read property file:\n%s", props)
    return props


def store_property_file(filename, props):
    """
    Write a property file (see load_property_file)
    :param filename:
    :param props:
    """
    config = "\n".join(prop + "=" + ",".join(vals)
                       for prop, vals in props.iteritems())
    atomic_write(filename, config)


def atomic_write(filename, contents):
    """
    Atomic write a file, by first writing out a temporary file and then
    moving into place.  The temporary filename is simply the supplied
    filename with ".tmp" appended.
    :param filename:
    :param contents:
    """
    ensure_dir(os.path.dirname(filename))
    tmp = filename + ".tmp"
    with open(tmp, "w") as f:
        f.write(contents)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, filename)


def move_file_if_missing(from_file, to_file):
    """
    Checks if the destination file exists and if not moves it into place.
    :param from_file:
    :param to_file:
    :return: Whether the file was moved.
    """
    _log.debug("Move file from %s to %s", from_file, to_file)
    if os.path.exists(to_file):
        _log.debug("File %s already exists, not copying", to_file)
        return False
    ensure_dir(os.path.dirname(to_file))
    os.rename(from_file, to_file)
    return True


def cmd_install_netmodules():
    """
    Install netmodules and Calico plugin.  A successful completion of the task
    indicates successful installation.
    """
    # Load the current Calico install info for Docker, and the current Docker
    # daemon configuration.
    install_config = load_config(NETMODULES_INSTALL_CONFIG)
    modules_config = load_config(AGENT_MODULES_CONFIG)
    mesos_props = load_property_file(AGENT_CONFIG)

    libraries = modules_config.setdefault("libraries", [])
    files = [library.get("file") for library in libraries]
    if "/opt/mesosphere/lib/mesos/libmesos_network_isolator.so" not in files:
        # Flag that modules need to be updated and reset the agent create
        # time to ensure we restart the agent.
        _log.debug("Configure netmodules and calico in Mesos")
        install_config["modules-updated"] = True
        install_config["agent-created"] = None
        store_config(NETMODULES_INSTALL_CONFIG, install_config)

        # Copy the netmodules .so and the calico binary.
        move_file_if_missing(
            "./libmesos_network_isolator.so",
            "/opt/mesosphere/lib/mesos/libmesos_network_isolator.so"
        )
        move_file_if_missing(
            "./calico_mesos",
            "/calico/calico_mesos"
        )

        # Update the modules config to reference the .so
        new_library = {
          "file": "/opt/mesosphere/lib/mesos/libmesos_network_isolator.so",
          "modules": [
            {
              "name": "com_mesosphere_mesos_NetworkIsolator",
              "parameters": [
                {
                  "key": "isolator_command",
                  "value": "/calico/calico_mesos"
                },
                {
                  "key": "ipam_command",
                  "value": "/calico/calico_mesos"
                }
              ]
            },
            {
              "name": "com_mesosphere_mesos_NetworkHook"
            }
          ]
        }
        libraries.append(new_library)
        store_config(AGENT_MODULES_CONFIG, modules_config)

    hooks = mesos_props.setdefault("MESOS_HOOKS", [])
    isolation = mesos_props.setdefault("MESOS_ISOLATION", [])
    if "com_mesosphere_mesos_NetworkHook" not in hooks:
        # Flag that properties need to be updated and reset the agent create
        # time to ensure we restart the agent.
        _log.debug("Configure mesos properties")
        install_config["properties-updated"] = True
        install_config["agent-created"] = None
        store_config(NETMODULES_INSTALL_CONFIG, install_config)

        # Finally update the properties.  We do this last, because this is what
        # we check to see if files are copied into place.
        isolation.append("com_mesosphere_mesos_NetworkIsolator")
        hooks.append("com_mesosphere_mesos_NetworkHook")
        store_property_file(AGENT_CONFIG, mesos_props)

    # If nothing was updated then exit.
    if not install_config.get("modules-updated") and \
       not install_config.get("properties-updated"):
        _log.debug("NetworkHook not updated by Calico")
        return

    # If we haven't stored the current agent creation time, then do so now.
    # We use this to track when the agent has restarted with our new config.
    if not install_config.get("agent-created"):
        _log.debug("Store agent creation time")
        agent_process = wait_for_process(AGENT_EXE_RE,
                                         MAX_TIME_FOR_AGENT_RESTART)
        install_config["agent-created"] = str(agent_process.create_time())
        store_config(NETMODULES_INSTALL_CONFIG, install_config)

    # Check the agent process creation time to see if it has been restarted
    # since the config was updated.
    _log.debug("Restart agent if not using updated config")
    agent_process = wait_for_process(AGENT_EXE_RE,
                                     MAX_TIME_FOR_AGENT_RESTART,
                                     PROCESS_STABILITY_TIME)
    if install_config["agent-created"] == str(agent_process.create_time()):
        # The agent has not been restarted, so restart it now.  This will cause
        # the task to fail (but sys.exit(1) to make sure).  The task will be
        # relaunched, and next time will miss this branch and succeed.
        _log.warning("Restarting agent process: %s", agent_process)
        agent_process.kill()
        sys.exit(1)

    _log.debug("Agent was restarted and is stable since config was updated")
    return


def cmd_install_docker_cluster_store():
    """
    Install Docker configuration for Docker multi-host networking.  A successful
    completion of the task indicates successful installation.
    """
    # Load the current Calico install info for Docker, and the current Docker
    # daemon configuration.
    install_config = load_config(DOCKER_INSTALL_CONFIG)
    daemon_config = load_config(DOCKER_DAEMON_CONFIG)

    if "cluster-store" not in daemon_config:
        # Before updating the config flag that config is updated, but don't yet
        # put in the create time (we only do that after actually updating the
        # config.
        _log.debug("Configure cluster store in daemon config")
        install_config["docker-updated"] = True
        install_config["docker-created"] = None
        store_config(DOCKER_INSTALL_CONFIG, install_config)

        # Update the daemon config.
        daemon_config["cluster-store"] = CLUSTER_STORE_ETCD_PROXY
        store_config(DOCKER_DAEMON_CONFIG, daemon_config)

    # If Docker was already configured to use a cluster store, and not by
    # Calico, exit now.
    if not install_config.get("docker-updated"):
        _log.debug("Docker not updated by Calico")
        return

    # If Docker config was updated, store the current Docker process creation
    # time so that we can identify when Docker is restarted.
    if not install_config.get("docker-created"):
        _log.debug("Store docker daemon creation time")
        daemon_process = wait_for_process(DOCKER_DAEMON_EXE_RE,
                                          MAX_TIME_FOR_DOCKER_RESTART)
        install_config["docker-created"] = str(daemon_process.create_time())
        store_config(DOCKER_INSTALL_CONFIG, install_config)

    # If Docker needs restarting, do that now.
    _log.debug("Restart docker if not using updated config")
    daemon_process = wait_for_process(DOCKER_DAEMON_EXE_RE,
                                      MAX_TIME_FOR_DOCKER_RESTART,
                                      PROCESS_STABILITY_TIME)
    if install_config["docker-created"] == str(daemon_process.create_time()):
        # Docker has not been restarted, so restart it now.  This will cause
        # the task to fail (but sys.exit(1) to make sure).  The task will be
        # relaunched, and next time will miss this branch and succeed.
        _log.warning("Restarting Docker process: %s", daemon_process)
        daemon_process.kill()
        sys.exit(1)

    _log.debug("Docker was restarted and is stable since adding cluster store")
    return


def cmd_get_agent_ip():
    """
    Connects a socket to the DNS entry for mesos master and returns
    which IP address it connected via, which should be the agent's
    accessible IP.

    Prints the IP to stdout
    """
    # IP and port are supplied as arguments
    # _log.debug("Arguments: %s", sys.argv)
    host, port = sys.argv[2].split(":")
    port = int(port)

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((host, port))
        our_ip = s.getsockname()[0]
        s.close()
    except socket.gaierror:
        _log.error("Unable to connect to master at: %s:%d", host, port)
        sys.exit(1)
    print our_ip


def initialise_logging():
    """
    Initialise logging to stdout.
    """
    logger = logging.getLogger('')
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
                '%(asctime)s [%(levelname)s]\t%(name)s %(lineno)d: %(message)s')
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(formatter)
    logger.addHandler(handler)


if __name__ == "__main__":
    initialise_logging()
    action = sys.argv[1]
    if action == "netmodules":
        cmd_install_netmodules()
    elif action == "docker":
        cmd_install_docker_cluster_store()
    elif action == "ip":
        cmd_get_agent_ip()
    else:
        print "Unexpected action: %s" % action
        sys.exit(1)
    sys.exit(0)
