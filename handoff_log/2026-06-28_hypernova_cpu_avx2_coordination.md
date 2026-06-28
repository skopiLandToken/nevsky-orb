# Hypernova migration — CPU/AVX2 blocker + server-upgrade investigation

**Author:** Elisha (DO droplet interface, `skopi-alpha-source-1`)
**Date:** 2026-06-28
**Status:** RESOLVED (path chosen) — Iosif ordered a Namecheap **Dedicated Server: Dual Xeon
Gold 5218 (Cascade Lake, AVX2+AVX-512), 128GB DDR4, 4×960GB SATA SSD**. That box becomes the
new home (ORB + Elisha native). HN VPS demoted to staging hop. Awaiting login/IP. **New migration
target = the dedicated box, NOT the HN VPS.**

### Migration readiness (DO side, 2026-06-28 ~05:11 UTC)
- Bundle `/opt/migration` REFRESHED: live `pg_dumpall` (nevsky_dev/umami/nextcloud, superuser
  = `nevsky` not `postgres`) + Lilith redis-dump.rdb re-dumped current. MANIFEST.sha256 rebuilding.
- 104G nominatim-flatnode + nominatim-pgdata staged & verified on HN.
- On new-box creds: push bundle (7.4G) + flatnode (104G) → dedicated; restore per
  /opt/migration/MANIFEST.md order; verify; DNS cutover.
- Other instance: if you have the new box's IP/creds or datacenter location, append below so we
  can route the 104G flatnode HN→new (fast LAN if co-located) vs DO→new (WAN, compressed).


## The blocker (root-caused, confirmed)

Claude Code will not run on Hypernova (`server1.orthodox.law` / 203.161.56.189). The native
`claude.exe` binary livelocks in a futex spin loop. Root cause is **CPU instruction set**, not
auth, not network, not a corrupt binary:

- HN `/proc/cpuinfo` model = **"QEMU Virtual CPU version 2.5+"**, flags expose **NO avx, avx2,
  sse4, fma, bmi**.
- DO (works) = "DO-Premium-AMD" with avx, avx2, sse4_1/2, fma, bmi1/2.
- Same binary, identical md5 (`75ccf0a95efb7ba6d646fa199fb10307`), works on DO, livelocks on HN.
- The Bun-compiled binary needs AVX2; HN's hypervisor masks it with the generic `qemu64` model.

## Two ways out

1. **Cheap fix (try first):** Namecheap support sets the VM's QEMU/libvirt CPU mode to
   `host-passthrough` (or `host-model`) so the guest inherits the host's real flags. Requires a
   VM stop/start. Iosif is filing this ticket. Risk: host CPU itself may be old/AVX2-less, or
   they refuse to change CPU mode on a shared-VPS tier.
2. **Better long-term fix:** move to a **Namecheap dedicated server** (bare metal = real CPU
   flags guaranteed). Investigation in progress (see below).

## Migration status (so the other instance isn't blocked)

- Bundle (ORB app + db) — copied earlier, verified.
- maps-pgdata, nominatim-pgdata — copied.
- **nominatim flatnode.file (104GB)** — WAS the "taking so long" stall: single TCP stream over
  the 60ms DO→HN link collapsed (5.8MB/s, 4.5h ETA). FIXED by adding `-z` to rsync — the file is
  ~558x compressible (heavy zero-padding between node IDs), so wire bytes collapse and effective
  throughput jumped to ~150-200MB/s. Script: `scripts/flatnode_resume.sh` (detached, auto-retry,
  final --append-verify integrity pass). Log: `/tmp/flatnode_resume.log` on DO.
- HN disk = 812 MB/s, 457GB free — not a constraint.
- Restore + service bring-up on HN: NOT yet run (waiting on flatnode + the CPU fix for claude).
- DO box stays live as rollback per the migration plan.

## Requested from the other Elisha/Yindo instance

- A web research pass is running on the DO side for Namecheap dedicated/VPS options with AVX2
  (results will be appended here). If you've already gathered any of this, append below so we
  don't duplicate.
- Decision input for Iosif: dedicated-server budget tolerance, and whether to wait on the
  support ticket (cheap) before ordering a dedicated box (certain).

## Findings log

### DO-side research result (2026-06-28)

**Critical:** Namecheap VPS = SolusVM-managed **KVM on a shared host** with a generic QEMU CPU
model set per-hypervisor, **not per-customer**. Namecheap does NOT expose `host-passthrough` to
VPS tenants and has no VPS tier that guarantees host CPU flags. **So fix #1 (the support ticket)
will most likely be refused — plan on it not working.** The reliable fix is a **dedicated server**
(bare metal = real CPU flags). Every current Namecheap dedicated CPU has AVX2.

Candidate dedicated boxes (all AVX2-confirmed, Phoenix AZ, skip paid cPanel add-on):

| Product | CPU | Cores | RAM | Disk | Price/mo (intro → renew) |
|---|---|---|---|---|---|
| **Xeon E-2236 NVMe** (recommended) | Xeon E-2236 Coffee Lake | 6c/12t @3.4GHz | 64GB | 2×960GB NVMe (~1.9TB) | ~$58.79 → ~$98.88 |
| Xeon E-2236 SATA | Xeon E-2236 | 6c/12t | 64GB | 2×960GB SATA SSD | ~$58.79 → ~$98.88 |
| Dual EPYC 7282 | 2× EPYC 7282 Zen2 | 32c/64t | 128GB | 4×1.92TB NVMe (~7.7TB) | ~$238.88 → ~$259.88 |

**Recommendation: Xeon E-2236 / 64GB / 2×960GB NVMe.** AVX2 fixes the livelock; 64GB is 4× the
VPS RAM (room for Postgres buffers + Nominatim cache + Valhalla); 1.9TB NVMe dwarfs the 104GB
maps data; real cores beat noisy-neighbor vCPU. Step up to Dual EPYC only if consolidating maps +
ORB + child-ORB inference on one box. Non-Namecheap providers (DO, Hetzner, OVH bare metal) also
solve AVX2 — the DO droplet already has it.

Sources: Namecheap VPS virtualization KB + SolusVM KB; dedicated-server configure/price pages.
