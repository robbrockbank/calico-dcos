import logging
import os

_log = logging.getLogger(__name__)

ETCD_BINARY_URL = "https://github.com/coreos/etcd/releases/download/v2.3.1/etcd-v2.3.1-linux-amd64.tar.gz"
ETCD_DISCOVERY = "etcd.mesos"
CALICOCTL_URL = "https://github.com/projectcalico/calico-containers/releases/download/v0.18.0/calicoctl"
CALICO_MESOS_URL = "https://github.com/projectcalico/calico-mesos/releases/download/v0.1.5/calico_mesos"
CALICO_NODE_IMG = ""
CALICO_LIBNETWORK_IMG = "calico/node-libnetwork:latest"
MESOS_MASTER = "master.mesos:5050"
CALICO_INSTALLER_URL = "http://172.25.20.11/installer"

# Development changes to the installer won't be picked up unless
# the binary uses a new name, or cache is disabled.
# Set this flag to False if you are updating the installer
# at CALICO_INSTALLER_URL without changing its name.
USE_CACHED_INSTALLER = False


class Config(object):
    def __init__(self):
        self.mesos_master = os.getenv('MESOS_MASTER', MESOS_MASTER)

        self.etcd_binary_url = os.getenv("ETCD_BINARY_URL", ETCD_BINARY_URL)
        self.etcd_service = os.getenv('ETCD_SRV', ETCD_DISCOVERY)

        self.zk_persist_url = os.getenv('ZK')

        self.max_concurrent_restarts = os.getenv('CALICO_MAX_RESTARTS', 1)
        self.calicoctl_url = os.getenv('CALICO_CALICOCTL_URL', CALICOCTL_URL)
        self.node_img = os.getenv('CALICO_NODE_IMG', CALICO_NODE_IMG)
        self.libnetwork_img = os.getenv('CALICO_LIBNETWORK_IMG', CALICO_LIBNETWORK_IMG)
        self.installer_url = os.getenv('CALICO_INSTALLER', CALICO_INSTALLER_URL)
        self.calico_mesos_url = os.getenv('CALICO_MESOS_URL', CALICO_MESOS_URL)
        self.allow_docker_update = os.getenv('CALICO_ALLOW_DOCKER_UPDATE', True)
        self.allow_agent_update = os.getenv('CALICO_ALLOW_AGENT_UPDATE', True)

# Instantiate a configuration class
config = Config()