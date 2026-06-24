"""
CloudLab Profile: Google-Trace Analysis Cluster

A 12-node cluster for the V2 Google memory-trace characterization campaign.
Unlike the generic custom-cluster profile (which attaches every dataset to
node0), this profile maps ONE persistent dataset to EACH node positionally:
node0 gets the first dataset, node1 the second, and so on. Each dataset is the
per-workload 2TB long-term store, mounted rw at /pdata on its own node, so the
12 workloads transform+analyze fully in parallel with no shared-storage funnel.

Each node also gets a local temp filesystem at /mnt/nvme (the fast scratch the
chunked transform uses) carved from local disk.

The profile does NOT clone any repo: the analysis code, DynamoRIO, and the venv
are scp'd to each node and installed via setup-host.sh AFTER provisioning. The
only inline startup work is preparing the /mnt/nvme and /pdata mount points.

Parameters:
  nodeCount   number of compute nodes (default 12)
  phystype    physical node type (default c220g5)
  osImage     OS image (default Ubuntu 24.04)
  pnodes      optional positional physical-node pinning
  datasets  ';'-separated dataset URNs, one per node positionally;
                   each attached rw at /pdata to node{i}. Empty entry => that
                   node gets no dataset (local scratch only).

Campaign mapping (clean 1:1 -- 12 workloads, 12 datasets, one workload per node,
each on its own 2TB dataset; no co-location, no spare):
  node0  gt-arizona     arizona
  node1  gt-bravo-a     bravo.a
  node2  gt-charlie     charlie
  node3  gt-delta       delta
  node4  gt-merced      merced
  node5  gt-sierra-a-3  sierra.a.3
  node6  gt-sierra-a-4  sierra.a.4
  node7  gt-sierra-a-6  sierra.a.6
  node8  gt-tahoe       tahoe
  node9  gt-tango       tango
  node10 gt-whiskey     whiskey
  node11 gt-yankee      yankee
Workloads are namespaced under /pdata (raw/<wl>, text/<wl>.txt, results/<wl>).
"""

import geni.portal as portal
import geni.rspec.pg as rspec

pc = portal.Context()
request = pc.makeRequestRSpec()

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

pc.defineParameter(
    "nodeCount", "Number of Compute Nodes",
    portal.ParameterType.INTEGER, 12,
    longDescription="Number of compute nodes (1-16), one workload per node."
)

pc.defineParameter(
    "phystype", "Physical node type",
    portal.ParameterType.NODETYPE, "c220g5",
    longDescription="Physical node type. c220g5 = 20c/40HT, 192GB RAM, "
                    "~480GB NVMe + 1TB HDD, 10/25GbE."
)

pc.defineParameter(
    "osImage", "OS image",
    portal.ParameterType.IMAGE,
    "urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU24-64-STD",
    [
        ("urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU24-64-STD", "Ubuntu 24.04"),
        ("urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU22-64-STD", "Ubuntu 22.04"),
        ("urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU20-64-STD", "Ubuntu 20.04"),
    ],
    longDescription="Ubuntu 24.04 (default). Ships a recent libstdc++ "
                    "(GLIBCXX_3.4.30+), so the prebuilt DynamoRIO release runs "
                    "with no shim."
)

pc.defineParameter(
    "datasets", "Per-node dataset URNs (positional)",
    portal.ParameterType.STRING, "",
    longDescription="';'-separated long-term dataset URNs, one per node in "
                    "order: the first attaches rw at /pdata to node0, the "
                    "second to node1, and so on. The number of URNs should "
                    "equal nodeCount. Empty entries leave that node with no "
                    "dataset."
)

pc.defineParameter(
    "nvmeMount", "Local scratch mount point",
    portal.ParameterType.STRING, "/mnt/nvme", advanced=True,
    longDescription="Where to mount the local temp filesystem used as fast "
                    "transform scratch."
)

pc.defineParameter(
    "pnodes", "Specific physical nodes (optional)",
    portal.ParameterType.STRING, "", advanced=True,
    longDescription="Pin nodes to specific physical machines. Comma-separated, "
                    "mapped positionally to node0,node1,... Empty entries are "
                    "left to the mapper. Instantiation FAILS if a pinned node "
                    "is busy."
)

pc.defineParameter(
    "pnodeDomain", "Physical node authority domain",
    portal.ParameterType.STRING, "wisc.cloudlab.us", advanced=True,
    longDescription="Authority domain for bare names in 'pnodes'."
)

pc.defineParameter(
    "clusterDomain", "Cluster authority domain (node placement)",
    portal.ParameterType.STRING, "wisc.cloudlab.us",
    longDescription="Pin every node to this cluster's component manager "
                    "(urn:publicid:IDN+<domain>+authority+cm). REQUIRED so the "
                    "nodes land on the SAME cluster as the persistent datasets -- "
                    "a dataset blockstore link cannot span clusters. Default "
                    "wisc.cloudlab.us (where the gt-* datasets live). Set empty "
                    "to let the mapper choose (only safe with no datasets)."
)

pc.defineParameter(
    "linkSpeed", "LAN link speed",
    portal.ParameterType.INTEGER, 0,
    [
        (0, "Any"),
        (10000000, "10Gb/s"),
        (25000000, "25Gb/s"),
    ],
    advanced=True,
    longDescription="Experiment-LAN link speed. The nodes share a LAN for "
                    "coordination (e.g. staging zips); analysis is node-local."
)

pc.defineParameter(
    "setupScript", "Setup Script URL",
    portal.ParameterType.STRING, "", advanced=True,
    longDescription="Optional URL to a setup script run on all nodes."
)

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

params = pc.bindParameters()

if params.nodeCount < 1:
    pc.reportError(portal.ParameterError("At least 1 node required", ["nodeCount"]))
if params.nodeCount > 16:
    pc.reportError(portal.ParameterError("Maximum 16 nodes", ["nodeCount"]))

# nvmeMount must be a safe absolute path that does not collide with the dataset
# mount (/pdata): it is interpolated into the inline prep shell and used as a
# blockstore mountpoint. Enforce a STRICT allowlist (only [A-Za-z0-9_/.-], so no
# whitespace/newlines/shell metacharacters), then COLLAPSE repeated slashes and
# normalize, so neither '//pdata' nor '/x/../pdata' nor a multiline value can slip
# past to collide with /pdata or hit the root.
import posixpath, re as _re
_nm = (params.nvmeMount or "").strip()
# \A...\Z (not $, which would allow a trailing newline) full-string allowlist.
_nm_collapsed = _re.sub("/+", "/", _nm)                       # // -> /
_nm_norm = posixpath.normpath(_nm_collapsed) if _nm.startswith("/") else ""
_nm_ok = bool(_re.match(r"\A/[A-Za-z0-9_./-]+\Z", _nm)) and \
    _nm_norm not in ("", "/", "/pdata") and \
    not _nm_norm.startswith("/pdata/")
if not _nm_ok:
    pc.reportError(portal.ParameterError(
        "nvmeMount must be a safe absolute path matching [A-Za-z0-9_/.-], "
        "not / or /pdata or under it",
        ["nvmeMount"]))
# Use the SANITIZED, normalized path at every sink (shell + Blockstore), never the
# raw param -- a trailing space/newline in params.nvmeMount must not reach them.
NVME_MOUNT = _nm_norm if _nm_ok else "/mnt/nvme"

# Reject more positional dataset URNs than nodes, and duplicate non-empty URNs
# (the same persistent dataset cannot be rw-mounted on two nodes).
_ds_check = [d.strip() for d in (params.datasets or "").split(";")]
_ds_nonempty = [d for d in _ds_check if d]
if len(_ds_check) > params.nodeCount:
    pc.reportError(portal.ParameterError(
        "datasets has more entries than nodeCount", ["datasets"]))
if len(_ds_nonempty) != len(set(_ds_nonempty)):
    pc.reportError(portal.ParameterError(
        "datasets has duplicate dataset URNs (a dataset is rw-mountable "
        "by only one node)", ["datasets"]))

pc.verifyParameters()


def pnode_component_id(pnode, domain):
    """Build a physical-node component_id URN from a name, or pass a URN through."""
    pnode = (pnode or "").strip()
    if not pnode:
        return None
    if pnode.startswith("urn:"):
        return pnode
    domain = (domain or "").strip() or "wisc.cloudlab.us"
    return "urn:publicid:IDN+%s+node+%s" % (domain, pnode)


def attach_dataset_rw(request, node, idx, urn, mount, cm_urn=None):
    """Attach one remote long-term dataset rw to 'node' at 'mount'.

    Each node-dataset pair gets its own RemoteBlockstore + dedicated link, so
    the 12 datasets attach to their 12 nodes in parallel with no funnel. The
    RemoteBlockstore fsnode is ALSO pinned to the dataset's cluster (cm_urn) --
    pinning only the compute node leaves the fs node unpinned, so the link
    between them is flagged as spanning clusters.
    """
    iface = node.addInterface()
    fsnode = request.RemoteBlockstore("ds%d-fs" % idx, mount)
    fsnode.dataset = urn
    if cm_urn:
        fsnode.component_manager_id = cm_urn
    fslink = request.Link("ds%d-link" % idx)
    fslink.addInterface(iface)
    fslink.addInterface(fsnode.interface)
    fslink.best_effort = True
    fslink.vlan_tagging = True


# Positional lists (mapped to node0, node1, ...).
_pnode_list = [p.strip() for p in (params.pnodes or "").split(",")]
_ds_list = [d.strip() for d in (params.datasets or "").split(";")]

# Inline node-prep run at instantiation (no repo dependency). Keeps it MINIMAL and
# non-destructive: create the scratch subdir and make ONLY the mount roots (not their
# contents) sticky-world-writable, like /tmp -- so the post-provision scp + setup-
# host.sh (which runs as the experiment user) can write under /mnt/nvme and /pdata
# without a hardcoded project group. No recursive chgrp/chmod (that would rewrite
# ownership+setgid on every file of a freshly-attached 2TB dataset). Errors are NOT
# suppressed except the optional /pdata chmod (a dataset may not be mounted yet on the
# spare node); the scratch dir is the only hard requirement here.
PREP_CMD = (
    "set -e; "
    "mkdir -p %s/scratch; "
    "chmod 1777 %s %s/scratch; "
    "[ -d /pdata ] && chmod 1777 /pdata 2>/dev/null || true; "
    "echo \"$(date) node prep done: $(hostname)\" >> /var/log/gtrace-prep.log"
) % (NVME_MOUNT, NVME_MOUNT, NVME_MOUNT)

# Cluster component-manager URN to pin node placement. Datasets are wisc-only and a
# dataset blockstore link cannot span clusters, so every node MUST land on the same
# cluster as its dataset. Empty clusterDomain => let the mapper choose (no pinning).
_cd = (params.clusterDomain or "").strip()
CM_URN = ("urn:publicid:IDN+%s+authority+cm" % _cd) if _cd else None

# ---------------------------------------------------------------------------
# Network: a single LAN for coordination (zip staging, control).
# ---------------------------------------------------------------------------

lan = None
if params.nodeCount > 1:
    lan = request.LAN()
    if params.linkSpeed > 0:
        lan.bandwidth = params.linkSpeed
    # Many per-node dataset blockstore links -> avoid strict bandwidth mapping.
    lan.best_effort = True

# ---------------------------------------------------------------------------
# Nodes: one workload per node, each with its own dataset + local scratch.
# ---------------------------------------------------------------------------

nodes = []
for i in range(params.nodeCount):
    node = request.RawPC("node%d" % i)
    node.disk_image = params.osImage
    if params.phystype:
        node.hardware_type = params.phystype

    # Pin the node to the dataset cluster's component manager (so dataset blockstore
    # links don't span clusters). Per-machine pinning via component_id (below) still
    # overrides placement within that cluster.
    if CM_URN:
        node.component_manager_id = CM_URN

    # Pin to a specific physical machine if given.
    if i < len(_pnode_list):
        _urn = pnode_component_id(_pnode_list[i], params.pnodeDomain)
        if _urn:
            node.component_id = _urn

    # Coordination LAN.
    if lan is not None:
        iface = node.addInterface("eth1")
        iface.addAddress(rspec.IPv4Address("10.10.1.%d" % (i + 1), "255.255.255.0"))
        lan.addInterface(iface)

    # Local fast scratch (all available local disk) at the sanitized mount path.
    bs = node.Blockstore("node%d-bs" % i, NVME_MOUNT)
    bs.size = "0GB"          # 0 => use all available space
    bs.placement = "any"

    # This node's OWN persistent dataset (positional), rw at /pdata.
    if i < len(_ds_list) and _ds_list[i]:
        attach_dataset_rw(request, node, i, _ds_list[i], "/pdata", CM_URN)

    # Minimal inline node prep -- NO repo clone (the analysis code + DR + venv are
    # scp'd to the node and installed via setup-host.sh AFTER provisioning, so the
    # profile must not depend on /local/repository). This just makes the mount
    # points ready with the right ownership so the later scp/setup step lands clean.
    node.addService(rspec.Execute(shell="sh", command=PREP_CMD))
    if params.setupScript:
        node.addService(rspec.Execute(
            shell="bash",
            command="curl -fsSL '%s' | bash" % params.setupScript))

    nodes.append(node)

pc.printRequestRSpec(request)
