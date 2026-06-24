# memtrace CloudLab profile

CloudLab geni-lib profile for the 12-node Google memory-trace analysis cluster.
`profile.py` provisions 12 c220g5 nodes at Wisconsin, each pinned to the cluster
and attached to its own per-workload 2 TB persistent dataset at `/pdata`, with
local NVMe scratch at `/mnt/nvme`. `setup-host.sh` is the post-provision node
bootstrap (DynamoRIO + Python venv), run after the analysis code is scp'd in.

This repo is public only so CloudLab can clone the profile for registration; it
contains no secrets.
