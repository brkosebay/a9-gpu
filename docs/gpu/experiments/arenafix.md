# Arena fix: the per-frame leak, and growth after the seal

Offline only. Two ICD + trampoline pairs carry the same arena fixes:

| pair | Mesa branch @ commit | ICD | trampoline | package (sha256) |
|---|---|---|---|---|
| (a) labwc desktop | `g5p-arena-fix` @ `b7d5b7c58b2` = `g5p-upload-fix` (labwc's `a2f00cda`) + fixes | `7bb4c436…` | `1d8f6098…` | `arenafix-desk-a9.tar.gz` `060bf6af…` |
| (b) WSI | `g5p-wsi-arena` @ `8d6242cc714` = `g5p-wsi@32b83afe10c` + 12-temp refusal + fixes + job split | `6d09b38d…` | `6be81453…` | `arenafix-a9.tar.gz` `31672658…` |

`scripts/gpu/arenafix.sh` builds both: `desk-*` for (a), the commands without a prefix for (b).

## Causes (file:line on the base each pair started from)

1. **Freed descriptor sets never gave back their GPU words.** In `g5p_desc.c:236` (`p->used += bytes`), the
   allocation is a bump. `g5p_desc.c:266` frees only the CPU side ("GPU words are reclaimed on pool reset").
   Zink allocates and frees sets in `FREE_DESCRIPTOR_SET` pools that it never resets. Each pool ran out, Zink
   made a new one in the arena, and the leak was ~53 KB per frame (the 72000/56000-byte pool allocations in the
   device log).
2. **The arena couldn't grow after the first job.** `g5p_winsys.c:264` freezes the arena at the seal, and
   `:286` refuses with "context sealed, no free committed span". The first kernels refused VM_BIND after a job;
   124j and later accept it when no job of the context is in flight.
3. **Released dedicated and imported BOs stayed mapped.** At `g5p_winsys.c:125`, after the first job a release
   only did `munmap`: "fd close is the only teardown". Every client dma-buf labwc ever imported stayed
   VM-mapped and pinned. The old WSI pair hit the kernel's per-VM limits: ENOSPC at frame ~5500 with
   imports replaced every 250 frames (1 GiB), and 256 mappings with 30 GPU-client windows.
   - Related: labwc imports its own GBM buffers back through EGL. `PRIME_FD_TO_HANDLE` then returns the same
     GEM handle for two BOs, so closing it needed a per-handle refcount.
4. **(b) only: a pass of more than ~123 draws failed.** At `g5p_record.c:81` (g5p-wsi), a job's fixed 16 KiB
   VDM stream fills at ~123 draws (~132 B per draw). Then `vkEndCommandBuffer` returns OUT_OF_DEVICE_MEMORY,
   Zink marks the batch lost, and it never recycles that batch's pools and resources. That was the 13-terminal
   freeze. The arena refusals that followed were a consequence, not the cause.
5. **(a)'s constraint, not a leak:** `g5p_record.c:582` (upload-fix) refuses `loadOp LOAD`. That base has no
   background LOAD object, so on it labwc must keep `WLR_SCENE_DEBUG_DAMAGE=rerender`. It can't split a pass
   either, so (a) keeps the ~123-draws-per-job ceiling. Keep the desktop under ~8 windows on (a).

## Fixes (one small commit each, `Generated-by: LLM`)

| fix | (a) `g5p-arena-fix` | (b) `g5p-wsi-arena` |
|---|---|---|
| high-water marks + the state behind a refusal | `e817cefbb6a` | `ea202dc77bf` |
| reuse freed descriptor words (`util_vma_heap` per pool, as in RADV) | `6dfe9fdad84` | `a59ecaae54d` |
| name each `g5p_job_begin` refusal | `36eb3d7c34c` (incl. "loadOp LOAD") | `87237f93ffc` |
| GEM handle refcount + `g5p_drm_vm_idle()` | `d0b1dcf386c` | `0181935d0c5` |
| chunk commits after the seal, only when idle | `97415d78081` | `8ed5716ebf3` |
| unmap + close released dedicated/imported BOs | `b7d5b7c58b2` | `e43c93098dc` |
| split a full job into a LOAD continuation | (no LOAD on this base) | `8d6242cc714` |

The fix files (`g5p_winsys.c`, `g5p_drm_transport.[ch]`, `g5p_desc.c`) are byte-identical on both branches.

**Growth rule:**

- A winsys `vm_lock` is held across seal + submit + wait (the render submit retires its job in the same call)
  and around every VM edit: chunk commit, dedicated BO, fixed-VA BO, dma-buf import, and release.
- Under the lock, `g5p_drm_vm_idle()` requires no accepted job outstanding and the last fence signalled, so
  no VM_BIND ever happens beside a job of that context. A context that lost a job stays refused.
- Chunks are committed up to `commit_cap` (default lazy: 512 MiB VA, 2×64 MiB at the seal, cap 192 MiB).
- Every commit after the seal prints `G5P VM arena grow:`. A refusal names its reason (commit cap, context
  not idle, larger than a chunk).
- The trampoline is built without the VM-edit text patch, because the ICD serializes its own edits.

## Offline evidence

**Build:**

- (a) uses `uploadfix.sh`'s recipe (the sealed Arena106 compile lines). Controls: combined107 → `9ca4766f`
  and g5p-upload-fix → `a2f00cda`, byte for byte. Two builds of the branch are identical.
- (b) uses `wsi.sh`'s meson build. A clean rebuild of every g5p object is identical. All 405 `g5p-gfx-test`
  scene files are byte-identical to the WSI base, and the round17 decoder is clean.
- Each trampoline's control build gives `081885cc`.
- Packaging twice gives the same sha256 for both packages: GNU tar, sorted, mtime 0, streamed into the
  container and checked against `SHA256SUMS` before packing.

**Soaks:** behind `af-kshim.so`, a kernel model. It enforces the idle-VM_BIND rule, the per-VM limits of 256
mappings and 1 GiB, and handle reuse like the kernel's idr. The soak fake node has room for 65536 jobs.
Verdicts come from `tools/gpu-arenafix/af-verdict.py`. A PASS needs:

- 0 refusals, 0 Zink errors and the arena receipt PASS;
- `busy_edits=0` (no VM edit beside a job), no stale handle, no ENOSPC;
- for runs of 5000 frames or more, flatness: the last watermark comes in the first 20 % of the run, and
  kernel mappings don't grow in the second half.

Logs are in `build/gpu/arenafix/soak/`. The verdicts are in `soak/VERDICTS.txt` (`arenafix.sh verdicts`). Stage
names:

- `desk`: (a), in the mesa117 tree the desktop runs;
- `desk-old`: `a2f00cda`/`10232194` as flown;
- `stage`: (b), in its client tree;
- `stage-wsi`: the WSI pair as flown (`ac0460f9`).

| run (frames) | new (a) `desk` | new (b) `stage` | old |
|---|---|---|---|
| `gl-dmabuf` 10000: labwc compositing an imported client, buffers replaced every 250 | n/a (no dma-buf on the desktop) | PASS: 126 maps / 123 unmaps, floor 22→22 maps | `stage-wsi` FAIL: floor 40→70 maps, ENOSPC at the 1 GiB limit, frame ~5500 |
| `gl-terms` 10000: 30 GPU-client windows (3 imports each) open/close, 25 cycles | n/a | PASS: 2256/2250 maps, peak 109 | `stage-wsi` FAIL: 256-mapping limit |
| `gl-terms-shm` 10000: 30 wl_shm windows, ~300 draws per pass | n/a (below 8 windows: `gl-terms8`) | PASS: 8 splits at 123 draws, peak 82 MiB | `stage-wsi` FAIL: 15 arena refusals + Zink error (job full) |
| `gl-terms-60` 10000: 60 windows | n/a | PASS: 1 post-seal commit to 192 MiB, peak 133 MiB | n/a |
| `gl-terms8` 10000: 8 wl_shm windows open/close | PASS: peak 57 MiB, 128 MiB committed | n/a | n/a |
| `gl-shm` / `gl-open` 10000: the wl_shm desktop, 12 MiB textures every 4 frames | PASS: peak 84 MiB, 128 MiB committed, flat after frame 1000 | PASS (same) | PASS on both (this mix doesn't leak on the old ICDs) |
| `vk-frames` 20000: Zink descriptors/staging/textures + client import/release + own export/import | PASS: 4560/4560 maps, floor 15→15 | PASS: floor 14→14 | FAIL on both: 1397/1209 VM_BINDs beside a job, ENOSPC |
| `vk-pools` 20000: FREE_DESCRIPTOR_SET churn after the seal | PASS | PASS | FAIL on both: 120002 OUT_OF_POOL_MEMORY, maxSets not enforced |
| `vk-grow`: 8 MiB slices with a job thread | PASS: 2 post-seal commits, 171 jobs between them, `busy_edits=0`, clean cap refusal at 192 MiB, same 21 slices again after free | PASS (39 jobs) | `stage-wsi` FAIL: "context sealed", no commit |
| `vk-teardown` 2000: export, import back, free | PASS: 4000/4000 maps | PASS | FAIL on both: import refused at 251 maps (iteration 119) |
| `vk-oldkernel`: a kernel that refuses VM_BIND after jobs | PASS (fails closed: commit failed, no hang) | PASS | n/a |
| `tramp` 300: through the trampoline | PASS: `G5P117_INIT result=ok`, no VM-edit patch | PASS | n/a |

Every new-pair run shows `busy_edits=0`, `stale=0`, `enospc=0` and `bo_kept=0`.

- `stage-desk-gl-dmabuf` is labwc's current ICD in the WSI tree with a dma-buf client. It fails on loadOp LOAD
  refusals (that base has no LOAD) and then sealed-arena refusals. This is why GPU clients need pair (b).
- Harness fixes made during this lane:
  - `af-vk` zeroes its job counter;
  - `pools` seals the arena first, as labwc does;
  - `grow` paces its allocations so that jobs interleave;
  - the verdict tests the floor of the kernel's mappings, not the peak. The peak of a cycling import set
    jittered 15→17 while the floor stayed at 15; the old pairs' floor rises.

## Device plan 1: desktop soak on pair (a), default lazy arena (**warm-safe**)

- **Changed variable:** only the two libraries in labwc's tree (`7bb4c436`/`1d8f6098` for `a2f00cda`/`10232194`).
- **Unchanged:** the kernel, the env (rerender, last-read), no linux-dmabuf.
- **Arena:** the eager stopgap (`/tmp/deskcfg/arena.env`) is removed and there is no `PVR_G5P_ARENA_*`
  line, so this is the default lazy arena.
- **Boot:** one warm kexec into the current desktop kernel through `boot-desk.sh`, the same as every desktop
  boot.
- **This replaces the live desktop session.** That is needed here: labwc's own ICD changes, and a client-only
  swap can't test it.
- **No owner step:** the load is automatic.

```sh
SSH=scripts/omarchy/ssh.sh
(cd build/gpu/arenafix/desk-build/package && shasum -a 256 -c SHA256SUMS)           # 060bf6af…
ARENAFIX=desk DESKTOP=1 scripts/omarchy/desk/boot-desk.sh <current desktop payload> <entry>
#   expect: arenafix=desk 7bb4c436e8d0f295 1d8f609844097ab2 / preload=0 / arena_env=0 damage=WLR_SCENE_DEBUG_DAMAGE=rerender
$SSH 'sudo -n grep -m1 G5P117_INIT /run/desk/labwc.log; sudo -n grep -m1 "G5P VM arena config" /run/desk/labwc.log'
#   expect: icd_sha256=7bb4c436… result=ok, and policy=lazy commit_cap_bytes=201326592
$SSH 'sudo -n sh /tmp/deskcfg/start-clients.sh'
# load: 4 foot windows printing 10 lines/s (wl_shm), closed and reopened every ~60 s (texture create/free)
feet() { $SSH "for i in 1 2 3 4; do sudo -n systemctl stop af-foot\$i 2>/dev/null; sudo -n systemctl reset-failed af-foot\$i 2>/dev/null; done; sleep 2
  for i in 1 2 3 4; do sudo -n systemd-run --quiet --uid=1000 --gid=1000 --unit=af-foot\$i /bin/sh /tmp/deskcfg/run-as-user.sh \
    /usr/bin/foot -W 60x15 -T af\$i sh -c 'while :; do date +%T.%N; sleep 0.1; done'; done"; }
mon() { $SSH "sudo -n grep 'LABWC-A9 FRAME' /run/desk/labwc.log | tail -1
  echo refusals=\$(sudo -n grep -E 'G5P VM arena: allocation|G5P refuse|G5P VM edit refused|G5P VM release' /run/desk/labwc.log | grep -vc FormatProperties)
  sudo -n grep -E 'G5P VM arena (watermark|grow)' /run/desk/labwc.log | tail -1 | cut -c1-200
  echo bad=\$(sudo -n dmesg | grep -cE 'RENDER-USER job=.* complete=-|terminal kind=(timeout|fault)')"; }
L=$($SSH 'sudo -n dmesg | wc -l')
for c in $(seq 1 40); do feet; sleep 60; mon | tee -a /tmp/desk/arenafix-desk-soak.txt; done   # ~40 min; stop early once n>=6000
$SSH "sudo -n grep -E 'G5P|LABWC-A9 FRAME' /run/desk/labwc.log" > /tmp/desk/arenafix-desk-labwc.txt
$SSH "sudo -n dmesg | tail -n +$((L + 1))" > /tmp/desk/arenafix-desk-dmesg.txt
```

- **PASS:**
  - the last `LABWC-A9 FRAME` has `n>=5000 failed=0`;
  - `refusals=0` throughout: no arena refusal, no `G5P refuse` other than Zink's format queries, no refused VM
    edit, no BO left to fd close;
  - the last arena line has `committed_bytes<=201326592` (192 MiB);
  - `bad=0`.
- **Informational:** whether watermark lines stop after the warm-up. A late `grow:` line within the cap is the
  designed behaviour, not a FAIL. Offline, the desktop mix stays at 2 chunks (128 MiB), peak used ~84 MiB.
  Report the watermark sequence from `mon`. A refusal here would point at a mechanism this lane did not model
  offline.
- **Stop rule:** a `terminal kind=timeout|fault` or `complete=-`. Keep the session and report the two files.
- **Control:** without the fix, the tahang1a S1 soak stopped at frame 1262 with its 64 MiB arena full
  (`STATUS.md` problem 1). Offline, the af-gl desktop mix does not reproduce that rate on the old ICD; its
  `gl-shm` passes there too. That makes this device soak the real test of fix 1, and `vk-pools` its offline
  proof.

## Device plan 2: WSI ladder W1–W4, labwc on pair (b), linux-dmabuf ON (**warm-safe**)

- **Kernel:** the WSI ladder's last kernel (`multiclient2-995d684abd`, or a later desktop kernel with
  multiclient2), one warm kexec.
- **Pair (b) in two places:**
  - labwc runs on (b) (`6d09b38d`/`6be81453`), with rerender and the default lazy arena;
  - clients run from `/tmp/arenafix-a9` (the same ICD).
- **Expected:** the ladder still draws clears and no geometry in client windows (a separate lane is on that).
  This plan checks only that nothing crashes, nothing is refused and the arena is bounded across import/release
  cycles.

```sh
SSH=scripts/omarchy/ssh.sh
(cd build/gpu/arenafix/package && shasum -a 256 -c SHA256SUMS)                      # 31672658…
ARENAFIX=wsi DMABUF=1 scripts/omarchy/desk/boot-desk.sh build/boot/m1n1-linux-multiclient2-995d684abd.bin multiclient2-995d684abd
#   expect: arenafix=wsi 6d09b38d6bf948ba 6be81453750c6bb3 / linux-dmabuf on / arena_env=0
$SSH 'mkdir -p /tmp/mc2 && chmod 0777 /tmp/mc2 && cp /tmp/arenafix-a9/a9-mesa/client-gpu-arenafix.env /tmp/mc2/client-gpu.env && echo PVR_G5P_DEBUG=refuse >> /tmp/mc2/client-gpu.env'
$SSH 'cat > /tmp/mc2/gpu-client.sh && chmod 0755 /tmp/mc2/gpu-client.sh' < scripts/gpu/multiclient2/gpu-client.sh
# pre-checks (as in wsi.md): Y 0 0, then 0 unresolved, then eglinfo on zink/G5P with icd_sha256=6d09b38d… result=ok
$SSH 'cat /sys/module/a9_gpu_drm/parameters/prime_experiment; sudo -n grep -c NO_LINUX_DMABUF /run/desk/gpu.env; sudo -n grep -c "LINUX-DMABUF disabled" /run/desk/labwc.log'
$SSH 'cd /tmp/arenafix-a9/a9-mesa && LD_LIBRARY_PATH=$PWD/lib64:/tmp/mesa102-desk/a9-mesa/lib64 ldd lib64/*.so* lib64/gbm/*.so bin/* | grep -c "not found"'
$SSH 'sudo -n grep -m1 G5P117_INIT /run/desk/labwc.log; sudo -n /tmp/mc2/gpu-client.sh root 30 /tmp/arenafix-a9/a9-mesa/bin/eglinfo -B -p wayland >/dev/null; sudo -n grep -E "renderer|G5P117_INIT" /run/desk/client-gpu.log | tail -2'
```

Define `rung_at` exactly as in `wsi.md` §"Rerun (orchestrator)". Its outputs land in `/tmp/desk/wsi-<n>-*`.
Then add a labwc arena check after each rung:

```sh
arena() { $SSH "echo refusals=\$(sudo -n grep -E 'G5P VM arena: allocation|G5P refuse|G5P VM edit refused|G5P VM release' /run/desk/labwc.log | grep -vc FormatProperties)
  sudo -n grep -E 'G5P VM arena (config|watermark|grow)' /run/desk/labwc.log | tail -1 | cut -c1-200
  sudo -n grep 'LABWC-A9 FRAME' /run/desk/labwc.log | tail -1; systemctl is-active desk-labwc" | tee -a /tmp/desk/af-ladder-arena.txt; }
B=/tmp/arenafix-a9/a9-mesa/bin
rung_at w1 90 3 5 $B/wsi-vk --frames 600;    arena      # Vulkan WSI, clear only
rung_at w2 90 3 5 $B/egl-wl --frames 600;    arena      # EGL -> Zink kopper, clear only
rung_at w3 90 3 5 $B/vkcube-wsi --frames 600 --check-at 30 --dump /tmp/mc2/vkcube-wsi-30.ppm; arena
rung_at w4 60 3 5 $B/weston-simple-egl;      arena      # ended by gpu-client's timeout
# import/release cycles: W1 and W2 again, 3 times each (every run imports 3-4 new client buffers and releases them)
for k in 1 2 3; do rung_at w1r$k 40 3 5 $B/wsi-vk --frames 300; arena; rung_at w2r$k 40 3 5 $B/egl-wl --frames 300; arena; done
```

- **PASS:** no crash, no refusals, a bounded arena:
  - every rung has `bad=0`;
  - each client exits: W1–W3 print their `frames=…` line, and W4 ends with `client rc=124`;
  - labwc stays `active` and its FRAME keeps advancing;
  - `refusals=0` after every rung, in labwc.log and in each client log (except Zink's D32 format query);
  - labwc's last arena line shows `committed_bytes<=201326592`.
- **Informational:**
  - the clients' own `result=` and `check frame=30` lines. W3's cube check is expected to fail while the
    geometry lane is open;
  - the fbdiff PPMs at +3 s and +5 s (`/tmp/desk/wsi-<n>-{a,b}.ppm`). W4 maps its window late, so its probe a
    may be empty;
  - whether the six repeat rungs add grow or watermark lines. Released imports should not accumulate; report
    the sequence.
- **Stop rule (as in `wsi.md`):** after `terminal kind=timeout|fault` or `complete=-`, skip the later rungs and
  keep the session.
- **Optional, last:** `$SSH 'sudo -n systemctl stop desk-labwc; sleep 3; sudo -n grep "G5P VM arena receipt" /run/desk/labwc.log'`
  ends this test session and prints labwc's receipt. Expect `bo_unmaps` close to `bo_maps`, `bo_kept=0` and
  `busy_refused=0`.

## Reproduce

- (b): `scripts/gpu/arenafix.sh configure`, then `all`: build, repro, test, tramp, stage, tools, soak, package.
- (a): `scripts/gpu/arenafix.sh desk-all`, after `tools`.
- `scripts/omarchy/desk/boot-desk.sh` takes `ARENAFIX=desk|wsi` and `DMABUF=1`.
