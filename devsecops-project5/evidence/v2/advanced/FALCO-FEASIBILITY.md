# Falco feasibility assessment

## Result

Falco runtime detection was **not operationally validated** in this Phase 4
run. No Falco alert is claimed and no simulated alert is included.

The project cluster was already absent when the approved continuation began.
The resource-management requirement says not to recreate it unless absolutely
necessary and to delete it immediately after testing. Recreating a cluster
solely to install an unverified runtime sensor would have increased VM
pressure without a reliable validation window, so Falco was not installed.

## Evidence collected

- The host has 6 CPUs, about 5.9 GiB RAM, and 4 GiB swap.
- The running kernel reported `CONFIG_BPF=y`, `CONFIG_BPF_SYSCALL=y`,
  `CONFIG_PERF_EVENTS=y`, `CONFIG_TRACEPOINTS=y`, and
  `CONFIG_KPROBE_EVENTS=y`. These are positive prerequisites for common Falco
  event sources.
- No `falco` executable was installed.
- No Falco image was present in the local Docker image cache at the feasibility
  check.
- No live kind node remained for checking the container runtime event source,
  host mounts, or the privileges required by the Falco DaemonSet.

## Security and operational conclusion

Kernel prerequisites alone do not prove that Falco can observe events from
containers inside this kind setup. A valid deployment would need a pinned
Falco image, a supported driver/event source, host access, and normally
privileged or equivalent node-level permissions. Those permissions would be an
intentional exception to the application's restricted namespace controls.

Therefore the correct result is **unverified feasibility**, not successful
detection and not a claim that Falco is inherently incompatible with kind.
The required runtime test remains future work.
