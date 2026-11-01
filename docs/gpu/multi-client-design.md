# Several GPU clients at once on the A9 (G5P): design

2026-11-01. Base: kernel branch `a9/gpu-desktop124` @ `c02a081b6f` (the labwc single-session
stack). Stage 1 passed on hardware (T1 in `experiments/logs/deskfold/RESULT.md`); stages 2 and 3
are built, not yet run on the device: see §11. Driver paths below are relative to
`drivers/gpu/drm/apple-g5p/` unless they start with `core/` (= `drivers/gpu/apple-g5p/`). Line
numbers in §5 refer to the base.

## 1. Decision

Each render context (one per VkDevice, so one per client process) gets its **own firmware slot
pair** (3D and TA, the two lowest free slots in 7..255) that points at its **own page catalogue**. The pair is bound
once, at the context's first job, by a **RAM-only DC3**: PA and tag go in the shared arrays, two
`{3,slot}` records go on the DC ring, the producer is stored, and the host waits on RAM only. There
is no doorbell and no register access. Every job then runs through the **one existing TA/3D channel
pair and the one PM record**, serialized exactly as today. The only difference per job is the slot
number the kernel already writes into W+4 and the start/finish packets.

This is iOS's slot model (one GART = one TA+3D slot pair, bound by DC3, kept resident), kept on our
single shared queue. It is **not** iOS's per-context channels and PM records. Those come later (§7, stage 4).

Per-job VM switch cost: zero host work. MMU caches are tagged by context (the slot is the `e138`
context field), so a switch needs no TLB invalidation. The SLC is probably context-tagged too: its
maintenance command takes a context (§2). That is inferred, not proven, and test T1 checks it.
Each VM keeps the proven RAM-only `0x84` tag path for its own edits.

## 2. Why not the alternatives

**A: one firmware context, with the kernel republishing the client's root before each job.** The proven
RAM-only path cannot switch roots. At each kick the firmware (`10017ac4`) compares `0x84[slot]` with
`0x85[slot]` and only issues an `e138` invalidate. **It does not reload CBASE.** CBASE (`e140/e148`)
is installed only by the DC3 handler (firmware-re README, round 15 §3; round 16 §1). Writing a new PA
into `0x86[slot]` and bumping the tag therefore leaves the hardware walking the old catalogue. A
would need a DC3 on every client switch, which is strictly worse than B's one DC3 per context
lifetime. The RAM-only variant of A (copying each client's PCEs into one fixed root before each job)
shares one context ID between clients. That has two consequences:
- it relies on a PC-level-only invalidate (`(S<<3)|4`, the only one the tag path issues) to drop the
  previous client's PD/PT cache entries for the same context. That is unverified, and if it fails,
  one client silently uses another client's translations;
- the SLC maintenance command is per context (`e230 = (ctx<<2)|3|0x800`, README "Maintenance order"),
  which is consistent with a context-tagged SLC. Two clients with different memory at the same VA
  under one context ID would alias in it.

**Full B (per-context channels and PM records, like iOS).** It would allow overlapping jobs and keep
per-context firmware state. It adds per-context CH, dependency-object and PM-record construction,
none of which has flown. It is not needed for several clients to render. That is stage 4.

**Other options considered.** One shared VM for all clients has no isolation. A userspace GPU server
that multiplexes command streams is a large userspace project for no gain over B.

## 3. Firmware facts this design depends on

| Fact | Source | State |
|---|---|---|
| User slots are 7..C−1 (≤255). Each render GART acquires 3D, then TA, and both slots point at the same catalogue PA. A slot stays resident until eviction or destruction. A job does not rebind. | iOS `AGXContextManager_acquireSlot` (`fffffff006c859a4`), called from `AGX3DCommandDescriptor_prepareContextAndWork`, `AGXTACommandDescriptor_preparePMAndWork` and `AGXComputeCommandDescriptor_prepareContextCDMSlot` (renamed in this lane). Publishes through `AGXAccelerator_publishGartContext` (`…6c81794`), then DC `{3,slot,0,0,0}` via `submitDeviceControlGated` + `waitDeviceControlConsumed` when firmware is live. README round 15 §1 | static RE, decompiled again here |
| DC3 handler: `e140=S`, `e148=PA[S]>>12`, copy host tag to `0x85[S]`, PC invalidate `(S<<3)|4`. PA array = 256 entries. | README round 15 §1 step 4; round 16 §1 | static RE; slots 7/8 proven on hardware (desktop124 attach line `firmware DC3 installed CBASE`) |
| The slot is a per-job operand. The TA start packet carries `STA`, the 3D start packet `S3D`, the PM tag6 packet `STA`. On every dispatch the firmware writes the selector bytes (`f078/136c/136d` TA, `f079/1368/1369` 3D) from W+4. | README round 16 §4 (start recipes), round 15 §1 (selector table, helpers `10017d6c`/`10018110`) | static RE; per-job slot writes already happen today (`g5p_ru_job(…, r->slot[e], …)`) |
| **The firmware drains the DC ring without a doorbell.** Its scheduler service trampoline `8000a444 → 10002504` snapshots all four host-to-firmware producers and calls the DC consumer `10001cf4` **before** TA/3D/CL. | README "DC dispatcher located" / "Scheduler path" | static RE |
| …and on hardware. In 8 of 8 attach failures that exported, the firmware consumed the DC3 pair on its own with no bell rung (consumer 3/3, `bells=0`, within 1.2 ms to about 1.2 s). Jobs ≥2 have their TA/3D rings consumed in 1–4 ms with no kick. | `docs/gpu/experiments/82-attach-reactivation-triage.md` §1–2; handoff §4.4 | proven before the first job; **after a retired job it is unproven** (test T1 measures it) |
| A host tag change for an attached slot is consumed at that slot's next job, with no register access. | handoff §4.3 (commit 10, desktop session) | proven for slots 7/8 |
| PM IDs and PM records are a separate namespace from MMU slots. PM requesters use the fixed reserved slots 1..6 (`f088=30102`, `f090=60405`), firmware-owned. The aux addresses in S must be mapped in the TA slot's VM. | README round 16 §2 | static RE; single-client proven |
| The TA start reloads PM context from aux (`240/248/2d8` ← `S.a8/b0/a0`, then `290/198/258`). TA finish saves bank0 into S/aux, and 3D restores bank1. TA-produced parameter data must survive until 3D completes. | README round 15 §4, round 16 §2 | static RE |
| No register access is safe after a retired job: the epoch is terminal from the first TA complete onwards. | handoff §1 and §4.3; `g5p_rv3_terminal()` | proven (two hard hangs) |

The fourth and fifth rows carry the design. If a post-retire DC3 turns out not to drain, §6 has the fallback.

## 4. Design

### 4.1 What is device-owned and what is per context

| Device-owned (one per epoch) | Per render context |
|---|---|
| TA/3D channel pages (CH, W, SKU, stamps), global TA/3D rings, DC ring | VM: own PC/PD/PT, own `vm->mutation` tag counters |
| **Channel sequence** (endpoints and the `seq−1` stamp guard belong to the shared pages) | slot pair `slot[0]`=TA, `slot[1]`=3D, `bound` flag |
| PM control, S record, list, F, **aux pages and parameter blocks** | render targets (HWRT main/secondary pages in its private range) |
| r74f/r74g first-job machinery, terminal latch | fence context, queued count, per-context job count |

**PM is device-wide, aux included.** Each TA reloads PM context from the aux pages that the
previous job's TA/3D saved (§3). If aux pages are shared, every job sees exactly the PM memory
sequence that the single-client runs proved, whichever VM runs it. Per-context aux pages would hand
job N+1 the PM state of a different job N. The same pages are mapped at `G5P_RU_PRIVATE_VA` in every
render VM, so the TA slot's VM always resolves them (fact row 7). This also saves 4 MiB of PM blocks
per client.

### 4.2 Bind (context's first job, in the executor, between jobs)

- **First render job of the epoch** (device bring-up): today's `g5p_rv3_attach()` is unchanged. It
  builds PM and channels, sends the DC3 for this context's pair, and runs the r74f/r74g one-shot
  machinery with its bell. That is the only path that may touch registers, and it runs before the
  first TA completes.
- **Every later context**, through the new `g5p_rv3_bind()`, with **no register access**:
  1. require the DC ring to be empty (`ctl[0] == ctl[4]`), and `0x86/0x84` for both slots to be zero;
  2. copy the firmware and sink catalogue entries into the VM root, clean every table page to PoC,
     clean this context's RT pages, `dsb(sy)`;
  3. compute the tag, write `0x86[s]=root PA` and `0x84[s]=tag` for both slots, write the records
     `{3,S3D}` and `{3,STA}`, `dma_wmb()`, store the producer `+2` with release semantics;
  4. wait on RAM only (`g5p_boot_drain()` plus sleep, bounded at 3 s) until the consumer equals the
     target. The DC3 handler's own `e140/e148/e138` writes are firmware MMIO under firmware power
     control. In the r82 triage runs, the firmware consumed DC3 while the GPC state read `0x10/0x11`
     (gated). It handles its own power for this. Only *host* register access after retirement hangs.
  5. check in RAM that the records are intact, `0x86 == root`, and `0x84 == 0x85 == tag` for both slots
     (a re-entrant copy of `g5p_r74g_check()`). Log `RENDER-V3 bind slots=TA/3D consumed_us=… tag=…`.
  6. On timeout: this context fails with `-ETIMEDOUT` and is marked bad. Set `d->rv3.dc_pending`, so
     later binds/unbinds return `-EIO` until the consumer catches up. Do **not** set `b->failed`:
     contexts that are already bound keep running, because the DC ring is not on their path.

### 4.3 Job execution

`g5p_rv3_execute()` runs as today with three changes. `first` means the device is not up yet. An
unbound context binds first (§4.2). The sequence, `pm_serial` and the PM rebuild come from the device
state. One job at a time stays the rule: TA(N+1) never overlaps 3D(N), because S, aux and the PM pool
are shared and TA output must survive until its own 3D (fact row 8). Overlapping needs a second PM
record (stage 4).

### 4.4 VM edits

These stay per context: `g5p_render_vm_idle()` (this context's `queued == 0`, not bad) plus
`g5p_render_invalidate()` on its own two slots, RAM only, as in commit 10. No device lock is needed:
the edit touches only this VM's tables and its own `0x84` words, and the firmware reads those only
at this context's next job. A bind happens inside this context's job (`queued > 0`), so binds and
edits of one context are mutually exclusive by the existing `f->lock` and queued count.

### 4.5 Unbind (stage 2)

At `DESTROY_CONTEXT` or file close, once `queued == 0`: write `0x86[s]=0` and `0x84[s]=0`, send the
two DC3 records, and wait on RAM only for consumption, exactly like §4.2. This is iOS's
`AGXGart_detachSlotsAndDestroyAddressSpace`: PA and tag zero, DC3 per slot (README round 15 §1). Run
it as an item on the render workqueue so it is serialized with jobs, single-writer on the DC ring,
and close never blocks. After consumption, free the tables and RTs and return the slots. On timeout,
keep everything and mark the context bad, with no device poison. The slot allocator becomes next-fit
over 7..255, so a slot is reused only after the allocator has gone round all 124 pairs.

### 4.6 Ordering, fences and fairness

- All jobs run in one global order on `render_wq` (ordered). Fences stay per context
  (`r->fence_context`). Cross-client sharing (a client's dma-buf sampled by labwc) is ordered by the
  existing implicit `dma_resv` fences and the explicit syncobj/sync_file.
- Stage 1 accepts two known problems. (a) `g5p_render_wait_inputs()` blocks the single worker, so one
  client's unsignalled input fence stalls everyone for up to `G5P_RU_INPUT_MS` (10 s). (b) The queue
  is FIFO with up to 16 queued jobs per context. At today's 0.3–1.8 s per desktop job, one greedy
  client can push labwc back by seconds.
- Stage 2: wait on inputs with `dma_fence_add_callback` before the job enters a run list, pick the
  next job round-robin across contexts, and cap queued jobs per context at 2 for contexts that are not
  the first claimant. Stage 4: replace this with `drm_sched` (one scheduler, credit limit 1, one entity
  per context, timeout above the job cap).

### 4.7 Fault isolation (honest scope)

| Event | Effect |
|---|---|
| Refused before hardware (validator, VA range, quota, admission) | that client only |
| A bind DC3 that is not consumed | that client fails; later binds and unbinds wait; bound clients continue |
| GPU timeout, fault or protocol error in any job | **device-terminal for every client**, as today: the terminal latch, no reset protocol |
| Client killed mid-job | the job finishes (it holds a context reference); then the context is retained (stage 1) or unbound (stage 2) |

A client can only reach its own VM plus the kernel-private ranges (aux, its RTs, the borrowed
firmware GiB, §6 R4). The kernel validators are the isolation line. Real fault isolation needs a GPU
recovery path, which is out of scope here.

## 5. Kernel changes

The plan as written before the build. What was actually built for stage 1, and where it differs,
is in §9.

**`a9-gpu-drm.c`**
- `struct g5p_drm`: add `struct g5p_rdev rv3` (`up`, `sequence`, `pm_serial`, `blocks`,
  `aux[G5P_RU_AUX_PAGES]`, `physical`, `dc_pending`, plus the latched PM knobs `pm_variant`,
  `memory_variant`, `cache_variant`) and `bool render_claimed`. `quarantined_context` stops meaning
  "render owner". Its compute exclusions (`drm-attach.inc:24/49`, `drm-submit.inc:351-364`, status at
  `:514`) test `render_claimed` instead.
- `g5p_get_info`: a new capability bit `DRM_G5P_CAP_RENDER_MULTI_CONTEXT` (bit 27). **That is the only
  UAPI change.** There are no new ioctls and `DRM_G5P_UAPI_VERSION` stays 1.

**`drm-render.inc`**
- `struct g5p_render` (`:145`): drop `blocks`, `pm_serial`, `sequence`, `aux[]`, `physical`, `owner`,
  `dc_target` and `v3_retain`. Rename `attached` to `bound` (this context's DC3 has been consumed).
  Add `jobs`.
- `g5p_render_create_context` (`:1100`): stop allocating per-context aux and physical pages. Allocate
  the device aux once (on the first create), then map the device aux pages into this VM at
  `G5P_RU_PRIVATE_VA` with `g5p_render_map_page()`. Slot allocation stays in the bitmap (`:1195`);
  next-fit comes in stage 2. Stage 3 deletes `capable(CAP_SYS_RAWIO)` (`:1122`).
- `g5p_render_release` (`:1066`): unmap the device aux and do not free it; free the slots only if the
  context never bound or has been unbound.
- `g5p_render_claim` (`:1601`) becomes `g5p_render_device_claim()`. The first accepted job does
  today's checks and the `d->accepted` cmpxchg once, then sets `render_claimed`. Later contexts pass
  if `render_claimed` is set and the device is neither failed nor stopped. **Delete** the per-context
  owner and the `quarantined_context == c` test.
- `g5p_render_attach_prepare` (`:382`): split it in two.
  - `g5p_rv3_device_prepare(d)` does PM (`g5p_ru_pm` on the device blocks), `g5p_ru_pm_variant`,
    `g5p_ru_channel_init` for both engines and the clean of the device pages.
  - `g5p_rv3_vm_prepare(d, r, start, &tag)` does the zero checks on `0x86/0x84`, the catalogue copy,
    table and RT clean, the tag, the PA/tag publish and the DC3 records.

  Running the current function for a second context would re-init the live channels and PM, which
  are shared.
- `g5p_render_vm_idle` (`:231`), `g5p_render_invalidate` (`:253`) and the tag publish at the end of
  `g5p_rt_create` (`:1009-1012`, which already cleans the RT pages and calls `g5p_render_invalidate`
  when attached): key on `r->bound` instead of `r->attached`. The bodies are unchanged. An RT created
  after a context's first job must keep publishing its tag.
- `g5p_create_render_target` (`:2064-2081`) and `g5p_destroy_render_target` (`:2128-2139`): drop the
  `mutex_trylock(&d->core->lock)`. The core lock is held for a whole job, up to 60 s, so with two
  clients any RT create or destroy would get `-EBUSY`. PM growth moves to the device: the executor
  grows `rv3.blocks` before the job's PM rebuild when the job's RT needs more blocks. Stage 1 may
  instead size the device PM for the panel-size RT at bring-up and refuse larger RTs with `-ENOSPC`.
- `g5p_render_destroy` (`:1252`): stage 1 keeps today's "a bound V3 context is retained until reboot".
  Stage 2 adds the §4.5 unbind and deletes the `writel(0x10, …0xb00)` doorbell unbind and the
  "one render attachment per boot" comment (`:1334`).
- Stage 3 deletes `!capable(CAP_SYS_RAWIO)` in the submit (`:1685`). `boot_experiment` stays as a
  module switch.
- File header (`:4`): replace "one render owner per boot".

**`drm-render-v3.inc`**
- `g5p_rv3_execute` (`:820`): `first = !d->rv3.up`. Otherwise, if `!r->bound`, call `g5p_rv3_bind()`
  and quarantine on failure as today. `seq = d->rv3.sequence + 1`. The PM rebuild uses the device blocks.
- `g5p_rv3_attach` (`:512`): calls `g5p_rv3_device_prepare` + `g5p_rv3_vm_prepare` and sets
  `d->rv3.up`. The protocol is unchanged.
- New `g5p_rv3_bind()` (§4.2). It must not call `g5p_r74f_admit`, `g5p_r74f_bell` or
  `g5p_r74f_ring`: `g5p_r74f_ring` reads `R1+0x14/R1+0x1000`, which is the post-retire hang.
- `g5p_rv3_publish_ta` (`:575`): `first = seq == 1` now refers to the device sequence, so only the
  epoch's first job gets the r74g grace.
- `g5p_rv3_state` (`:70`): fill in `device_up`, `bound`, the device sequence and the context's `jobs`.

**`core/render-user-serial.h`** (`g5p_ru_v3_admit`; host test
`patches/gpu-render-v3/host/test_render_serial_v3.c`)
- **Delete** `s->quiet && !s->attached → TERMINAL`. Once job 1's TA completes, the epoch is quiet for
  good, so today no second context can ever attach. The clause exists only because attach rang a bell.
- **Replace** `slot_ta != 8 || slot_3d != 7` with `slot_3d >= 7 && slot_ta >= 7 && slot_ta != slot_3d &&
  slot_ta <= 255 && slot_3d <= 255`. The pair need not be adjacent: `:1196` takes the two lowest free
  bits, and unbinds leave holes. The `render_user_slot_variant` swap stays refused through `variant`.
- **Replace** the executor's `attached == !!sequence` check with `device_up == !!device_sequence`.
  `MIXED` uses the context's `jobs`.

**`drm-vm.inc`** (stage 3)
- `G5P_MAX_MAPS` 256 → 4096 (`a9-gpu-drm.c:39`). Replace the linear `g5p_vm_maps_obj()` scan with a
  per-VM xarray from object to mapping count.
- Pinned memory: replace the per-VM `G5P_MAX_PINNED_BYTES` check (`:379`) with a device-wide budget.
  A per-object VM-pin count in `struct g5p_bo` adds an object's size once, at its first mapping in any
  VM, and removes it at its last unmap. Module parameter `render_pinned_mib` (default 1024). The 1 GiB
  per-VM mapping sum stays as VA bookkeeping.

**Keep:** RAWIO on compute and diagnostic ioctls (`drm-submit.inc:217`) and on the `enable` parameter
(`a9-gpu-drm.c:621`). Legacy non-V3 render profiles stay single-context: they are refused once
`rv3.up` is set or a second render context exists.

## 6. Risks

- **R1. A post-retire DC3 is not drained.** That is the one new firmware assumption. Static RE says DC
  is serviced first in the same pass that drains TA/3D, and the pre-job evidence is 8/8. If T1 shows no
  consumption, the fallback is **pre-bound slot roots**. During job 1's attach (bell allowed) the
  kernel binds K extra pairs, each to a kernel-owned root page. When a context is assigned a pair, the
  kernel copies its PCEs into that pair's root (RAM) and bumps the tag's PC field. That uses only the
  proven tag path. It relies on the PC-level invalidate covering re-pointed PCEs, but only at slot
  reuse, never between two clients that are alive at the same time.
- **R2. Slots other than 7/8 have never run a render job.** The firmware arrays are sized 256 and the
  selectors take any 8-bit slot, so this is low risk, and T1 tests it directly.
- **R3. Shared PM state across VMs.** PM ID 0 and record 0 serve every VM. Any firmware state keyed by
  PM ID together with the previous TA slot is unknown. Fallback: a PM record per context (iOS's
  `ParamBuffer` ID pool, README round 15 §4).
- **R4. Dropping RAWIO opens the firmware GiB.** `g5p_rv3_vm_prepare` copies the firmware catalogue entry
  (`G5P_RU_FIRMWARE_CI`) into every user VM, so shader code can address firmware memory: the rings,
  `0x86` PA array and W. With RAWIO gone, any render-node client, and WebGL1 content in a browser, is
  root-equivalent. Fix in the same stage as dropping RAWIO: give each VM a kernel-built PD under that
  PCE that maps only the pages GPU requesters use (PM control, list, F to start with). An MMU fault in
  the stage-3 run names any page that is missing. The shared PM aux is also mapped writable in every
  VM, so a shader that writes there corrupts PM state for every client. That is fatal today with one
  client as well, but it now crosses clients.
- **R5. One timeout kills every client** (§4.7). A long client job (bounded at 30–120 s) also stalls
  labwc, because there is no preemption.
- **R6. Memory leak in stage 1.** Contexts are retained until reboot, together with their pinned BOs
  (arena about 64 MiB plus swapchains). That limits stage 1 to a few client launches per boot on
  2 GiB. Stage 2 fixes it.
- **R7. Userspace.** labwc-a9 runs with `LABWC_A9_NO_LINUX_DMABUF=1` (wl_shm clients only). Cross-process
  dma-buf import into labwc's VM, and sampling a LINEAR imported image in the ICD, have never flown.
  GTK4's GL renderer and Firefox WebRender need GLES3 and fall back to CPU rendering. `foot` renders
  with pixman (CPU). Realistic GPU clients today are GLES2: `es2gears_wayland`, `weston-simple-egl`,
  `glmark2-es2-wayland`, and alacritty with `renderer = "gles2_pure"`.
- **R8. Cross-VM sampling coherence.** A client's output read by labwc through another context relies
  on the firmware's per-job finish cache maintenance, the same as CPU↔GPU today. T2 checks that the
  window content changes from frame to frame.

## 7. Staged plan

1. **Stage 1: a second client renders alongside labwc (root).** §5 minus the stage 2/3 items: device
   render state, shared aux, split prepare, `g5p_rv3_bind`, the admission edits, device claim, no core
   lock in RT create/destroy. Contexts are retained on close. Host tests: the updated
   `test_render_serial_v3.c`, plus a mock bind (consumed / never consumed / ring busy, no register
   access on any path).
2. **Stage 2: clients can come and go.** RAM-only unbind (§4.5), next-fit slots, device PM growth in
   the executor, input waits off the worker, round-robin selection and a per-context queue cap.
3. **Stage 3: unprivileged clients.** Drop RAWIO on render context create and submit, the narrowed
   firmware window (R4), the device-wide pinned budget, `G5P_MAX_MAPS` 4096, and the capability bit.
4. **Stage 4 (later): throughput and robustness.** `drm_sched`, TA(N+1)/3D(N) overlap with a second PM
   record, per-context channels, and GPU recovery.

## 8. Device test plan (orchestrator)

Common setup: the stage-N payload. Run labwc exactly as in the desktop124 session
(`scripts/omarchy/labwc-a9-session.sh`, `configs/labwc-a9/gpu.env`). Collect netconsole and dmesg as
usual.

**T1 (stage 1, kernel only, labwc unchanged).** Wait until labwc shows its first frame receipt, and
take a framebuffer probe of labwc's composited window box (the desktop124 probe, box at 512,832).
Then, as root over SSH, run the typed-V3 fixture client `a9-first-frames --node <render node>
--frames 10` (`tools/mesa-a9/`, the latency101 fixture with a readback oracle) on its own DRM file.
It does not read `GET_STATUS.claimed` (checked); only its header comment assumes one context per
boot. Then run it twice more one after another, then two copies at once.
PASS means all of the following:
- labwc's `RENDER-USER attached TA=8 3D=7 … firmware DC3`, then, for each fixture run, one new
  `RENDER-V3 bind slots=10/9 consumed_us=… tag=…` line (then 12/11, 14/13, …), whose RAM check shows
  `0x85 == tag` for both slots;
- every fixture frame MATCHes;
- every `RENDER-USER job=… complete=0`, with labwc and fixture jobs interleaved in the log;
- labwc frame receipts keep advancing during and for at least 60 s after the fixtures;
- a second framebuffer probe after the fixtures finds the same labwc window box with the same pixels.
  Shared PM state going wrong (R3) would leave labwc's jobs at `ret=0` and show only on the panel;
- no `RENDER-V3 terminal kind=timeout|fault`, no `refused`, no `3d-refused`, and SSH stays alive.

The concurrent fixtures map their targets at identical VAs in different VMs, and each frame
re-prefills its own target before submit. A job that wrote through another context's translations
(TLB or SLC aliasing) would leave its own target showing the prefill, and the frame would mismatch.
Record the publish-to-consume time of each post-retire DC3: that is the new firmware fact. If a bind logs `ret=-110` with the DC ring stuck at
`prod ≠ cons`, R1 has happened: keep labwc running, confirm it is unaffected, and stop.

**T2 (stage 1 plus userspace).** Start labwc with linux-dmabuf allowed (`LABWC_A9_NO_LINUX_DMABUF`
removed from `gpu.env`). Run `es2gears_wayland` (or `weston-simple-egl`, then `glmark2-es2-wayland`)
with the Mesa lines of `gpu.env` (`LD_LIBRARY_PATH`, `VK_DRIVER_FILES`, `GBM_BACKENDS_PATH`,
`DRIRC_CONFIGDIR`, `PVR_G5P_EXPERIMENTAL`, `PVR_G5P_GROUPS`) and `WAYLAND_DISPLAY`. Also try
`vkcube --wsi wayland` if the ICD exposes Wayland WSI.
PASS means all of the following:
- the client reports a Zink/g5p renderer, not llvmpipe;
- its bind line appears and its jobs complete (`=0`), interleaved with labwc's;
- two framebuffer probes a second apart both find the client window, with different contents (the
  gears move);
- labwc keeps presenting after the client exits.

If labwc logs a dma-buf import failure, that is the R7 userspace path, not the kernel.

**T3 (stage 2).** Start and stop the T2 client 20 times. Each exit logs
`RENDER-V3 unbind slots=… consumed_us=…`. `free -m` returns to its baseline, give or take one client.
Slots advance next-fit. labwc is unaffected throughout.

**T4 (stage 3).** Repeat T2 with the client running as an unprivileged user in group `render`
(render node mode 0660). The client renders, compute and diagnostic ioctls still return `EPERM`, and
no MMU fault appears with the narrowed firmware window. A fault address in the log names a page the
window is missing.

## 9. Stage 1 as built

Kernel branch `a9/gpu-multiclient1` (worktree `/src/wt/multiclient1`), two commits on `c02a081b6f`:

- `dde89cfc8b` drm/apple/g5p: let several render contexts share the GPU
- `1f38929f3b` drm/apple/g5p: add a temporary pre-bound slot-pair fallback (TIP)

Payload `build/boot/m1n1-linux-multiclient1-1f38929f3b.bin`, sha256
`98c29281338f9b0ed455d4ec2245d171e355c26fd6784ddc987e9c03585ac31a`. Boot entry
`build/gpu/multiclient1/kernel/multiclient1-1f38929f3b-entry/` (per-file sha256 in
`build/gpu/multiclient1/kernel/entry.sha256`; `Image.gz` `eae464a1…`). Built and packaged by
`scripts/gpu/multiclient1-build.sh verify|package`: the desktop124 config
(`build/kernel-gpucombined118/config`) and cmdline base, only `linux_a9.entry` changed. Release
`7.3.0-rc1-g1f38929f3b75`. Offline checks: `scripts/gpu/multiclient1-check.sh`.

### What changed and where

- **Device render state** (`drm-render.inc`, `struct g5p_rdev`, `d->rdev`). It holds `up`/`v3`
  (who brought the device up), the device `sequence` and `pm_serial`, the PM pool
  (`blocks`/`physical`) and the 5 aux pages, and `dc_pending`/`dc_wait` for a bind the firmware has
  not consumed. Only the executor writes the job state. `rdev->lock` guards the pool and the aux pages.
  The aux pages are allocated at the first render context create and mapped at
  `G5P_RU_PRIVATE_VA` in every render VM. The pool grows under the lock (`g5p_rdev_grow()`), from
  context create (`render_user_pm_blocks`) and from RT create with kernel geometry, and never
  shrinks. This differs from §5, which moved growth into the executor. Growth while another
  context's job runs is safe, because the job in flight keeps its pages and the next job rebuilds
  the pool. Every new page is cleaned to PoC.
- **Context** (`struct g5p_render`). It owns its firmware state: `slot[2]`, `bound`, `dc_target`,
  `jobs`, and `retained`. `retained` is the binding's context and `drm_dev` reference, taken
  before the producer store. A context the firmware may hold therefore survives fd close until
  reboot (R6). `claimant` replaces `owner`. The other per-job scratch (`target/submitted/…`) is
  unchanged.
- **Device claim** (`g5p_render_device_claim()`). The first accepted job runs today's checks and
  takes `d->accepted`, `b->user_compute_owned` and `d->quarantined_context` as before, and sets
  `d->render_claimed`. Later jobs of any context pass without the core lock while the device is
  neither failed nor stopped. The per-context owner test is deleted. A claimant destroyed before
  touching hardware drops its reference but not the claim. The compute exclusions are unchanged
  (`accepted` stays set). The r8 epoch reports `claimed` from `render_claimed`.
- **Split prepare.** `g5p_render_device_prepare()` covers PM, the variant, both channels and the
  cleans, and runs only while the device is down. `g5p_render_vm_prepare()` covers the ring/slot
  check, catalogue entries, table and RT cleans, the tag, and the PA/tag/records. Both are shared
  by the legacy attach and the V3 attach (`g5p_render_attach_prepare()`).
- **Bind** (`g5p_rv3_bind()`, `drm-render-v3.inc`). It runs for an unbound context's first job on
  an up device. It refuses while an earlier bind is pending, publishes through
  `g5p_render_vm_prepare()`, takes the retain reference, and stores the producer with release
  semantics. It then waits with `g5p_rv3_dc_wait()`: `g5p_boot_drain()`, a RAM sample,
  `usleep_range(100, 200)`, bounded at 3 s, with a closing sample at the deadline. Finally it
  checks RAM with `g5p_ru_bind_check()`. Nothing touches a register: no `g5p_r74f_*`, no
  `g5p_prepare_kick`, no `readl`. A failure sets `job->rv3.bind_failed`. The context goes bad, but
  `b->failed` and the terminal latch are not set, so bound contexts keep running.
- **Bind helpers** (`core/render-user-bind.h`, new, pure). The slot allocator
  `g5p_ru_slot_pair()` gives the two lowest free slots in 7..255, with the lower one as 3D. The
  file also holds the record words, publish, sample, busy check, the r74g-equivalent RAM check,
  and the wait step.
- **Executor** (`g5p_rv3_execute()`). `first = !rdev->up`, `seq = rdev->sequence + 1`, and the PM
  rebuild uses the device pool. Receipts are gated by the context's `jobs`, so each client's first
  8 jobs print. The receipt sequence is the device's (`job->rv3.seq`).
- **Admission** (`core/render-user-serial.h`). `quiet && !attached` is now
  `quiet && !device_up`: the device cannot come up once the epoch is quiet, but a bind is allowed.
  The slots must be any two distinct slots in 7..255. `MIXED` covers another profile on this
  context or a device brought up by a legacy profile. `STATE` requires
  `device_up == !!sequence`, `bound == !!jobs`, and that a bound context implies an up device.
- **Core-lock trylocks removed**: RT create, RT destroy, and the LINEAR64 RT create in the submit.
  Their edits are RAM only, as in `g5p_render_invalidate()`. The claim's trylock remains only for
  the first claim. `g5p_render_run()` still tries the core lock for 2 s, which is fine because jobs
  are serialized.
- **Legacy profiles** stay single-context. They are refused at submit and in the runner once the
  device is up via V3, or on a context that did not bring it up. A bound V3 context's
  `DESTROY_CONTEXT` returns `-EBUSY` (retained).
- **UAPI**: `DRM_G5P_CAP_RENDER_MULTI_CONTEXT` is bit 27, with an Asahi-style kernel-doc comment,
  and is reported with `TYPED_RENDER_V3`. There are no new ioctls and the UAPI version is still 1.
  `CAP_SYS_RAWIO` stays.

Offline: the driver builds with `W=1` and no warnings. checkpatch `--strict` on the code is clean.
The only remaining warnings are the `Co-Authored-By` trailer form and MAINTAINERS for the new
header. The host tests are `scripts/gpu/multiclient1-host/test_render_bind.c` (177 checks: the
slot allocator, publish and check against a mock DC3 firmware, consumed, never consumed, ring busy,
slot used, torn tag, ring wrap, the spare batch) and `test_render_admit.c` (83 checks).
`audit_ram_only.py` walks every function reachable from the bind, adopt, spares, RT
create/destroy, VM-edit and PM-rebuild paths (27 functions) and finds no register access.

**Merging with the latency lane.** `a9/gpu-latency123` and `123b` auto-merge in `drm-render.inc`
and `drm-vm.inc`. `drm-render-v3.inc` has one textual conflict: their
`#include "drm-latency123.inc"` and `g5p_rv3_fw_touch()` sit next to the phase-name and sample
edits here. Neither side touches the other's firmware staging or pstate code. After the merge,
also call `g5p_rv3_fw_touch(b)` in `g5p_rv3_dc_wait()`'s loop, because the bind waits on the same
shared pages that their touch makes visible to the firmware.

### Fallback: `render_user_spare_pairs` (temporary)

This module parameter (0..32, default 0) is read at the epoch's first TYPED_V3 job.
`g5p_rv3_spares()` binds that many extra pairs, each to a zeroed kernel-owned catalogue page with
tag `0x00400000`. They go out as one RAM-only batch ahead of job 1's attach, and the host waits for
the firmware to drain it, which is the self-drain regime of the r87b attach. It cannot share the
attach's batch, because `g5p_r74f_ring()` refuses a ring that holds anything but the attach's two
records. Job 1's attach, bell and r74g checks then run unchanged. None of them assumes a DC ring
index: `g5p_r74f_admit()` checks only the bell state for DC3, and `g5p_r74f_ring()` and
`g5p_r74g_check()` work from the `start` the attach passes, which is the index after the drained
spare batch.

A later context then runs `g5p_rv3_adopt()` instead of a DC3. It checks in RAM that the spare still
names its page with the spare tag, copies the VM root into the spare page and swaps it in as the
VM root. It moves the context to the spare's slots and returns its own slots. Then
`g5p_render_invalidate()` publishes a tag that differs from the cached one, so the firmware
invalidates the slots' PC level at the context's first job. The pair count bounds the client
launches per boot.

### Device plan (orchestrator)

T1 ends the current desktop124j session (a warm kexec), and a fault in any job is device-terminal
for every client (R5), so labwc can die with a failing fixture.

**Boot.** Same path as the current desktop:

```
/tmp/desk/boot-desk.sh build/boot/m1n1-linux-multiclient1-1f38929f3b.bin multiclient1-1f38929f3b
SSH=scripts/omarchy/ssh.sh
$SSH 'uname -r; cat /sys/module/a9_gpu_drm/parameters/render_user_spare_pairs'   # 7.3.0-rc1-g1f38929f3b75, 0
$SSH 'sudo -n dmesg | grep -E "RENDER-USER attached|RENDER-V3 (terminal|refused|bind)"'
$SSH 'sudo -n grep "LABWC-A9 FRAME" /run/desk/labwc.log | tail -1'
$SSH 'sudo -n /tmp/mesa102-desk/a9-mesa/bin/fbprobe --card /dev/dri/card0' | tee /tmp/desk/mc1-fb-before.txt
$SSH 'free -m'
```

Expected: labwc's `RENDER-USER attached TA=8 3D=7 … firmware DC3 installed CBASE` exactly as on
desktop124j, no bind line, and the `wl_shm_probe` window found by fbprobe (`result=PASS`, note
`box=` and `digest=`).

**T1.** Upload the fixture (the latency101 build, sha256 `58fd788c…7442`) and run it as root on its
own DRM file, next to labwc:

```
BIN=build/gpu/latency101/mesa-a9-frames/bin/a9-first-frames.aarch64
$SSH 'mkdir -p /tmp/mc1 && cat > /tmp/mc1/a9-first-frames && chmod 0755 /tmp/mc1/a9-first-frames' < "$BIN"
$SSH 'sha256sum /tmp/mc1/a9-first-frames'
run() { $SSH "cd /tmp/mc1 && sudo -n timeout -s KILL 400 ./a9-first-frames --node /dev/dri/renderD128 --abi v3 --timeout-ms 20000 --frames 10 --out /tmp/mc1/$1.bgra --report /tmp/mc1/$1.txt >/tmp/mc1/$1.out 2>&1; echo $1 rc=\$?"; }
run a1                     # T1a: one client
run a2; run a3             # T1b: two more, one after another
$SSH 'cd /tmp/mc1 && for n in a4 a5; do (sudo -n timeout -s KILL 600 ./a9-first-frames --node /dev/dri/renderD128 --abi v3 --timeout-ms 20000 --frames 10 --out /tmp/mc1/$n.bgra --report /tmp/mc1/$n.txt >/tmp/mc1/$n.out 2>&1; echo $n rc=$?) & done; wait'   # T1c: two at once
$SSH 'sudo -n dmesg | grep -E "RENDER-V3 (bind|spares|start|end|terminal|refused|3d-refused)|RENDER-USER (attached|job=)"' > /tmp/desk/mc1-dmesg.txt
$SSH 'grep -h "caps=" /tmp/mc1/a1.txt; tail -3 /tmp/mc1/a*.out'
$SSH 'sudo -n /tmp/mesa102-desk/a9-mesa/bin/fbprobe --card /dev/dri/card0' | tee /tmp/desk/mc1-fb-after.txt
$SSH 'sudo -n grep "LABWC-A9 FRAME" /run/desk/labwc.log | tail -1; free -m'
# again at least 60 s after the last fixture exits:
$SSH 'sudo -n grep "LABWC-A9 FRAME" /run/desk/labwc.log | tail -1'
```

PASS requires all of the following:
- Every run exits with `rc=0` (every frame MATCH), and `caps=` in the report has bit 27
  (`0x8000000`) set.
- There is one bind line per fixture run, in this form:
  `RENDER-V3 bind slots=10/9 ret=0 consumed_us=<N> tag=<T> host=<T>/<T> cached=<T>/<T> dc=<S>-><S+2>/<S+2> bad=0 pc=<pa> mode=ram-dc3 end=v3`.
  The slots go `10/9`, `12/11`, `14/13`, then two of `16/15` and `18/17` in either order. `S` is the
  DC ring index (2 after labwc's attach if nothing else used the ring) and advances by 2 per bind. `cached` equal to `tag` in both slots is the tag copy (the
  firmware's DC3 handler ran). `slots=` is TA/3D. The `RENDER-V3 start` line before it prints
  3D/TA with `attach=ram-dc3`.
- `consumed_us` of each bind is the new firmware fact: the publish-to-consume time of a DC3 after a
  retired job, with no doorbell. Record all five.
- Every `RENDER-USER job=… complete=0`. labwc's jobs (`slots=8/7`) and the fixture's (`slots=10/9`, …)
  interleave. There are no `RENDER-V3 terminal kind=timeout|fault`, `refused` or `3d-refused`
  lines, and SSH stays up.
- labwc's `LABWC-A9 FRAME … n=` keeps advancing during the fixtures and for at least 60 s after.
- The second fbprobe shows the same `box=` and `digest=` with `result=PASS`. Shared PM state
  going wrong (R3) would show only here, since labwc's jobs would still end `complete=0`.
- `free -m` drops by no more than a few tens of MiB, because five fixture contexts are retained
  (R6).

A fixture `rc=3` means the fixture gave up waiting (its own cap is 30 s per frame), not that the
kernel failed: under the desktop tuple (`render_user_hard_ms=60000`) a job may run up to about
120 s, and in T1c a fixture frame can wait behind labwc's and the other fixture's jobs. The job
still finishes, because it holds its context. Read that job's `RENDER-USER job=… complete=` line
before calling it R1 or R3.

**If a DC3 is not consumed (R1).** The bind line reads `ret=-110 consumed_us=-1 … dc=<S>-><S>/<S+2>`, and
that fixture fails with rc 4. Keep labwc running and confirm it is unaffected: its jobs still end
`complete=0`, FRAME `n` keeps advancing, and fbprobe is unchanged. A later fixture logs
`RENDER-V3 bind refused … an earlier bind is pending` while the consumer stays put. If a later
fixture binds normally, the firmware consumed the earlier pair late, which means a slow drain,
not R1: report the times. Stop T1 on this boot.

For a real R1, use the fallback on a fresh epoch with the same entry. Write the parameter after
the kexec and before labwc's first job (it has no effect later on that boot):

```
cp /tmp/desk/boot-desk.sh /tmp/desk/boot-desk-spares.sh
# in the copy, right after the line that appends "echo 0 > $P/boot_hb116_seed" to $sets, add:
#   sets="$sets"$'\n'"echo 6 > $P/render_user_spare_pairs"
# (the same write by hand: $SSH 'echo 6 | sudo -n tee /sys/module/a9_gpu_drm/parameters/render_user_spare_pairs')
/tmp/desk/boot-desk-spares.sh build/boot/m1n1-linux-multiclient1-1f38929f3b.bin multiclient1-1f38929f3b
$SSH 'cat /sys/module/a9_gpu_drm/parameters/render_user_spare_pairs'   # 6, before labwc's first job
```

Expected: `RENDER-V3 spares pairs=6 ok=6 ret=0 consumed_us=<N> dc=<S>-><S+12> first=10/9 last=20/19 end=v3`
before labwc's attach line, which then reads `TA=8 3D=7` as before. Rerun T1 (five runs fit in six
pairs). Each bind line reads `RENDER-V3 bind slots=10/9 ret=0 consumed_us=0 tag=<T> cached=00400000/00400000 … mode=spare end=v3`.
PASS is as above, and MATCHing frames show that the firmware walked the adopted root after the PC
invalidate. If the spare batch is not consumed (`spares … ret=-110`), labwc's first job fails and
the epoch is dead: the fallback has failed too, so kexec back to `desktop124j`.

## 10. Stages 2–4 on `drm_sched` and `drm_gpuvm`

Stage 1 is shaped so later stages can move onto the common DRM components the way Asahi did,
without redoing the binding.

- **Objects.** `struct g5p_rdev` is the device-level state behind one scheduler (Asahi's
  `GpuManager`: shared channels and the PM record). `struct g5p_render` is the queue: it owns its
  VM reference, its fence context and its firmware slot pair, the way an Asahi `Queue` owns its
  `GpuContext`. Bind and unbind are RAM-only helpers that take a context and touch nothing outside
  its own slots and the DC ring, so they can be called from any scheduler callback.
- **Stage 2 (clients come and go).** The RAM-only unbind (§4.5) runs as the context's last
  ordered item: today a work item on `render_wq`, later the entity teardown
  (`drm_sched_entity_flush()`/`_destroy()`, then the unbind before the context's final put).
  Next-fit slots stay in `g5p_ru_slot_pair()`'s caller. Waiting on inputs with
  `dma_fence_add_callback` and round-robin with a per-context cap are exactly what
  `drm_sched_job_add_dependency()`, `drm_sched_job_add_implicit_dependencies()` and the scheduler's
  entity selection provide. Stage 2 can adopt them directly instead of hand-rolling them.
- **Stage 3 (unprivileged).** `struct g5p_vm` becomes a `drm_gpuvm`. VM_BIND map/unmap becomes
  `drm_gpuvm` split/merge ops, and the RAM-only PTE edit plus `g5p_render_invalidate()` tag
  publish become the ops' step callbacks and a single flush. The private ranges (low sink, aux,
  RTs, the firmware GiB) become kernel-reserved regions of the gpuvm. `drm_gpuvm_bo` replaces
  `g5p_vm_maps_obj()`'s linear scan and the per-object VM pin count behind the device-wide pinned
  budget. VM-private BOs share the gpuvm's `resv`.
- **Stage 4 (throughput).** One `drm_gpu_scheduler` for the shared TA/3D channel with a credit
  limit of 1, and one `drm_sched_entity` per `g5p_render`. `run_job` is today's
  `g5p_rv3_execute()` split at the publish: bind if unbound, PM rebuild, build and publish, return
  the hardware fence. The RAM-only wait moves into a completion worker that signals that fence.
  `timedout_job` is the terminal latch, since there is no reset. TA(N+1)/3D(N) overlap needs a
  second PM record. Per-context channels (full B) mean one scheduler per context ring, like
  Asahi's per-queue firmware rings. The slot binding stays where it is, owned by the context.

## 11. Stages 2 and 3 as built

Kernel branch `a9/gpu-multiclient2` (worktree `/src/wt/multiclient2`) on deskfold
(`a9/gpu-mcfold2` @ `f410c38330`, verified on hardware), seven commits, tip `995d684abd`:

| commit | what |
|---|---|
| `e836175ddc` | free closed render contexts, schedule them fairly, drop RAWIO (stages 2 and 3) |
| `60cab62979` | log the device's pinned total when a VM is freed |
| `8d3aeac3c3` | let VM_BIND UNMAP run beside a context's queued jobs |
| `81e730726f` | fail blocked render jobs as soon as admission closes |
| `67defaee93` | lose only the context of a job that times out (fault isolation, firmware alive) |
| `f64d32b9a3` | recover the GPU in the boot after a job stops the firmware (**opt-in, default off**) |
| `995d684abd` | test hook: lose one client job on the host (`render_user_test_stall_job`, default off) |

Payload `build/boot/m1n1-linux-multiclient2-995d684abd.bin`, sha256
`8acb674e29da6a0b8687855f9037cbb2d15fee85485a4a6e0644dd0ef921c4db`. Entry `multiclient2-995d684abd`
(`build/gpu/multiclient2/multiclient2-995d684abd-entry/`, per-file sha256 in
`build/gpu/multiclient2/entry.sha256`; `Image.gz`
`624ef5c1c5995b163ed2a903837d7b07e57fdc7a0e4a25f74e3943843d82469e`). Release
`7.3.0-rc1-g995d684abd67`. Config byte-identical to
`build/kernel-gpucombined118/config`. Cmdline, DTB and initramfs are deskfold's; only
`linux_a9.entry` differs. Built and packaged by `scripts/gpu/multiclient2-build.sh verify|package|check`.

### 11.1 What changed

**Release on close (stage 2).** `g5p_render_close()` (`drm-render-unbind.inc`) runs when a TYPED_V3
context's handle goes away: `DESTROY_CONTEXT` (which now returns 0 instead of `EBUSY`) or the file's
close (`g5p_drm_postclose()`). It stops VM and render-target edits on the context (`detached`, and
`vm->resident_gone`, which also keeps the VM from getting another context) and queues the
context's **close item** behind its jobs. The close item (`g5p_render_close_run()`) decides with a pure
plan (`core/render-user-close.h`):
- **release**: the firmware never held the pair. It drops the claim's reference if this was the
  first claimant.
- **unbind**: the pair is bound and checked, and every job has retired. It calls
  `g5p_rv3_unbind_slots(d, r->slot, 2, "close")` (the mcfold2 helper: PA and tag to zero, one
  `{3,slot}` record per slot, one producer store, the RAM-only wait, then a check that the cached
  tag is zero), under the bounded core-lock trylock. On `ret=0` it drops the binding's reference and
  the claim's reference. The last reference then runs `g5p_render_release()`: the VM tables, render
  targets and mappings are freed (the BOs are unpinned) and the slots go back to the allocator.
- **retain**: a bind was published but never verified, the context is bad, the epoch noted a timeout
  or fault, the device failed or stopped, admission is closed (reboot quiesce), a DC batch is
  pending, or the lock could not be taken. Everything stays until reboot, as in stage 1, and the
  kexec quiesce detaches it then.

What the firmware needs before a pair is reused (firmware-re README round 15 §1, "Linux sequence"
step 8, and iOS `AGXGart_detachSlotsAndDestroyAddressSpace` / `acquireSlot`):
- every use of the pair retired: the close item runs after the context's last job;
- PA and tag published as zero, and the DC3 consumed. The handler then installs CBASE 0, copies the
  zero tag and invalidates the slot's PC level;
- the slot recycled only after that, so an old unbind never clears a new owner's PA.

The "reuse epoch" (manager+0x28) is host-side bookkeeping in the iOS kext. The firmware never reads
it, so Linux has nothing to publish for it. iOS pushes a detached slot back on a LIFO free stack, so
it reuses the most recently freed slot first. The kernel does the same with the lowest free pair,
which is returned to the bitmap only in `g5p_render_release()`, after a verified unbind. A bind tag
is never 0 (`g5p_render_vm_prepare()` bumps it), so a zero cached tag after an unbind proves that the
handler ran. Whether the DC3 bind's PC-level invalidate also drops a reused context ID's PD/PT
cache lines is not proven. iOS relies on it, and the slot-reuse test (§11.4 T5) checks it.

**VM_BIND MAP beside queued jobs (stage 2).** `g5p_render_vm_idle(c, map)`: once a job has retired
on the context's binding (`live_maps`), a MAP may run while the context's jobs are queued. It only
adds entries for VAs that no accepted job uses, and its tag reaches the firmware at the next kick.
This is how a compositor imports a client buffer while its previous frame is in flight. UNMAP,
render-target create and destroy still need the context idle. Before the first retired job (the
epoch's job 1 checks that the tables did not change since its attach, and a bind computes its tag
from them), nothing may edit the tables while a job is queued.

**Scheduler (stage 2).** `drm-render-sched.inc` and `core/render-user-sched.h`, shaped like
`drm_sched` so that stage 4 can swap it in:
- Each render context is an entity holding a FIFO of accepted jobs, plus at most one close item,
  which runs after its last job.
- One worker (a delayed work item on the ordered `render_wq`) serves the run list round-robin. It
  takes the first entity whose head job is ready, moves that entity to the tail, and runs the job to
  completion on the one shared channel (one credit).
- Inputs are waited for with `dma_fence_add_callback()` on the head job, which is
  `drm_sched_job_add_dependency()` in all but name. The callback re-queues the worker.
- The deadline is still `G5P_RU_INPUT_MS` (10 s) after the submit. The worker re-arms itself for the
  earliest deadline. A failed input or an expired deadline makes the job ready with that error, so
  it fails before the hardware and its fence still signals in order.
- Closed admission fails every queued job with `-ESHUTDOWN` without its inputs, as before.

With N clients ready, a frame of the compositor waits for at most N−1 other jobs, whatever the
queue depths. The per-context queue cap stays at 16. `g5p_render_run()` is the old work function,
minus the in-worker input wait.

**Unprivileged render (stage 3).**
- *Firmware window* (`core/render-user-window.h`, `g5p_render_fw_entry()`). A render VM no longer
  copies the firmware's catalogue entry 0x387. That GiB holds the firmware image, the root and
  shared pages (rings, the PA/tag arrays, W), the MMU table alias and the PIO pages (Fender, AIC
  timer, AIC software interrupt). The entry now points at a directory that the kernel builds once,
  at the first bind, from the firmware's own leaf entries, copied bit for bit. It holds only the PM
  control page (`0xe1c025c000`), the PM list (`0xe1c0260000`) and the free list F (`0xe1c0264000`).
  All render VMs share it, and it is never edited. It costs a directory page and one leaf page.
  The three pages themselves stay GPU-writable, as before: a job that scribbles over PM state
  still hurts every client (R4).
  `render_user_fw_window` (0600, latched at context creation): 0 is the stage 1 mapping and then
  needs `CAP_SYS_RAWIO`; 1 is the window (default); 2 maps no firmware page (an experiment, to find
  out whether the PM pages are needed at all). The VA0 sink entry is unchanged. The RE says PM's
  requesters use the firmware-owned slots 1..6 for their own translations, and only the aux reload
  goes through the TA slot's VM (README round 16 §2). So mode 2 may well work too, but mode 1 is the
  safe default. One line reports the window: `RENDER-V3 fw-window pages=3 missing=0 leaves=1 dir=<pa>`.
- *No `CAP_SYS_RAWIO`* for render `CREATE_CONTEXT` and TYPED_V3 submission in modes 1 and 2. The
  legacy profiles (they touch registers), compute, the diagnostic ioctls and the `enable` parameter
  keep it. `boot_experiment` still gates submission. `DRM_G5P_CAP_RENDER_UNPRIVILEGED` (bit 28) is
  reported when TYPED_V3 is on and the window is not in mode 0.
- *Pinned memory* (`drm-vm.inc`). Each VM keeps a per-object mapping count (an xarray, replacing the
  linear scan). An object's size counts against the device budget `render_pinned_mib` (0600, default
  1024) at its first mapping in any VM of any file, and leaves it at its last unmap. Our own dma-buf
  imports back as the same GEM object, so a client buffer that labwc samples counts once. The
  per-VM 768 MiB limit and the 1 GiB VA bookkeeping stay. `G5P_MAX_MAPS` is 4096. Each freed VM
  logs `G5P VM release maps=… pinned=…K device_pinned=…K`.

**VM_BIND UNMAP beside queued jobs (`8d3aeac3c3`).** A compositor drops a client buffer whenever the
client releases it, usually while its own previous frame is queued. Once a job has retired on the
binding, an UNMAP clears its entries and publishes its tag at once, but the mapping's object stays
referenced and pinned (a zombie on the context) until the jobs that were queued at that moment have
retired (`g5p_render_reap()`). A job that still used the range faults on the GPU instead of reaching
freed memory. A bad context keeps its zombies until it is released.

**Closed admission fails blocked jobs at once (`81e730726f`).** The reboot quiesce's frontend hook and
the FINAL teardown's DETACH hook kick the scheduler, so a job waiting on an unsignalled input fails
with `-ESHUTDOWN` without waiting for its 10 s deadline.

**Fault isolation, firmware alive (`67defaee93`, `core/render-user-loss.h`).** The firmware has no
abort and the TA and 3D channels are FIFO, so a job can be lost alone only while the firmware is
alive, and nothing may be published behind its stuck phase. A phase timeout of a job other than the
epoch's first, with the event stream not silent, no fault event and no protocol error, and with
`render_user_drain_ms` valid for the context's bounds (`g5p_ru_drain_valid()`: the desktop's
latency101 tuple, hard 60000 + obs 2000, allows the default 45000; without latency101 at most 8000),
loses only that job and its context:
- the job ends `-ETIMEDOUT`, the context is bad (its client gets `-EIO`; pair and memory are kept
  until it closes), and nothing is noted terminal: `RENDER-V3 lost seq=… engine=… slots=…: context lost`;
- the scheduler holds every other job (never refusing it) and drains the channel RAM-only
  (`g5p_rv3_drain()`): a late TA gets its 3D published, so the 3D sequence and the PM state stay as the
  firmware expects; a late 3D clears the channel. The held jobs then run;
- a silent stream, a fault, a refused 3D or the drain bound end the epoch as before
  (`RENDER-V3 drain dead … `). A context whose lost job drained may be unbound and freed at close;
- only a failed device fails later jobs; one context's lost job or failed bind does not. The reboot
  quiesce treats a blocked channel as a wedged epoch.

**In-boot recovery (`f64d32b9a3`, opt-in: `render_user_recover`, default 0).** The two hardware wedges
seen with several clients (deskfold boot e46d5121: vkcube seq 1977, the XFB hold job seq 2283) silenced
the firmware, so they still end the epoch above. iOS ends that state with a firmware stop, a platform
power cycle and a new firmware boot. `core/recover.inc` does the same from two proven pieces: the
reboot quiesce's freeze, IRQ mask and PMGR gfx gate cycle (`g5p_kq_*`), then the bootstrap's own stages
(wake, map, arm, load, release, events, control) as the sysfs runner orders them; id, sample and
irq-test are not rerun, the firmware image is reused, and the old `struct g5p_boot` stays allocated.
With `render_user_recover=N > 0`, a job other than the epoch's first that stops the firmware (silent
stream, a fault, or a drain that died) loses its context and sets `rdev->recovering`. The worker
recovers before any pick (`g5p_rv3_recover()`): every context's binding belonged to the old epoch, so
each is unbound in RAM and its binding reference dropped; the firmware window's leaves are rewritten
from the new tables in the same pages; the aux pages and PM pool are zeroed; spare pairs are freed; the
terminal latch is reset. Submissions and the claim are accepted meanwhile and the job is held. After
the second bootstrap the recovery asks the dirty-start claim gate again (a new `recover` event: a
generation that voids every proof of the last, ALIVE included, and counts as a witnessed cycle), so the
held jobs run only with ALIVE proven in the new generation; the first one attaches the new epoch. Lines:
`GPU-RECOVER why=lost-job ret=… stage=… ms=… gen=… end=recover`, then `RENDER-V3 recovered n=… contexts=…
unbound=…`. A failed recovery leaves the device failed (`RENDER-V3 recover failed`). At most N per boot
(each leaks the old epoch's firmware pages, a few hundred KiB). **Never run on hardware**; with 0 every
new branch is dormant. Known hazard when enabled: lock-free readers of `g->boot` outside the render
paths may see it NULL during the swap (the 20 Hz desk-read files `result`, `render_status` and
`recovery_status` are NULL-safe).

**Test hook (`995d684abd`, `render_user_test_stall_job`, default 0).** Latched at context creation: a
context created while it is N gives up on its N-th job's TA after 1 ms on the host. The firmware sees
nothing different and completes the job late, so the host takes the stuck-job path above
deterministically (`RENDER-V3 test-stall seq=… job=N slots=…`). Never the epoch's first job. The
stalled wait is the legacy single bound, which has no latency101 grace, so the loss cannot be
classed silent (`LOSS_CONTEXT`, never `LOSS_DEVICE`); the drain then watches the event stream from
its own start. The close plan passes `bad && !lost_drained`, so the drained context unbinds at close.

**Not changed.** One job at a time on the shared channel and PM record. A timeout of the epoch's
first job, a silent stream or a fault is still device-terminal by default (§4.7). The legacy
`DESTROY_CONTEXT` doorbell unbind stays for the non-V3 profiles, which are diagnostics. The kexec
quiesce (mcfold2) still detaches whatever is held at reboot. The kernel config is unchanged (no
`DRM_SCHED`).

**GPU Wayland clients (goal 4): what is in place and what is not.**
- Kernel: PRIME export/import is the driver's own (`drm-prime.inc`, `prime_experiment`, set by the
  desk launcher); our dma-buf imports back as the same GEM object, so labwc sampling a client
  buffer maps the client's object and the device budget counts it once (`e836175ddc`). MAP and UNMAP
  beside labwc's queued jobs are the import/release path. Nothing else is needed in the kernel.
- labwc: `tools/orchard-labwc-a9/patches/labwc/0002-a9-single-context-gles2-session.patch` already
  gates linux-dmabuf on `LABWC_A9_NO_LINUX_DMABUF`; starting labwc without that line advertises it.
  No labwc code change. Whether wlroots' GLES2 renderer on EGL/Zink exposes import formats, and
  whether the ICD samples a LINEAR imported image, is the unflown R7 path: T2 tests it.
- Client env: `configs/labwc-a9/client-gpu.env` (the Mesa lines of `gpu.env`: the uploadfix ICD pair
  `a2f00cda…` + trampoline `10232194…` as staged by `UPLOADFIX=1`, `PVR_G5P_GROUPS=last-read`,
  `MESA_LOADER_DRIVER_OVERRIDE=zink`, `EGL_PLATFORM=wayland`), run by
  `scripts/gpu/multiclient2/gpu-client.sh <user> <seconds> <cmd…>`, which also grants a non-root
  user labwc's socket only.
- Packaging still to do (not in this branch): `es2gears_wayland` (mesa-demos) or
  `glmark2-es2-wayland` must be on the device; if neither is installed, `pacman -S mesa-demos` or
  `glmark2` on the iPad (Arch ARM), linked against the system libwayland, with the staged a9-mesa
  loaded through `LD_LIBRARY_PATH`. vulkaninfo is not installed either.

### 11.2 Offline checks (`multiclient2-build.sh check`)

- `scripts/gpu/multiclient2-host/test_render_close.c`: 938 checks, 0 failures. It covers
  the close plan for every input, and bind → close → rebind of 10/9 with another catalogue (the new
  CBASE and tag, labwc's 8/7 untouched, even with an equal tag). It runs 20 start/stop cycles across
  a ring wrap, every one on 10/9 and leaving all user slots zero. It also covers an unbind that is
  never consumed (slots and memory kept, the next client on 12/11, later closes retained while
  pending, released once the firmware catches up), a skipped tag copy (retained) and the
  zero-tag case.
- `test_render_sched.c`: 49 checks, 0 failures. It covers RR order, the FIFO inside a
  context, a blocked head that does not hold up others and is not overtaken by its own later jobs,
  the earliest-deadline wake, close after the last job and only once, re-enlisting, the greedy
  bounds (≤ 1 job ahead of labwc with one greedy client, ≤ 2 with two) and the shutdown drain.
- `test_render_window.c`: 66152 checks, 0 failures. It covers the three pages with the
  firmware's entries bit for bit, every other mapped firmware page of the GiB unreachable (image,
  root, rings, PA arrays, W, PIO), and a firmware without the PM metadata pages.
- `audit_mc2.py`: PASS, 63 functions from 35 roots (close, the close item, the unbind,
  release, postclose, the scheduler pick, its callbacks and push, VM_BIND map and unmap, the window
  builder, bind, the pin budget, VM release, the loss decision, the lost record and the drain, and
  the recovery's frontend half: `g5p_rv3_recoverable`, `g5p_rv3_lose_recover`, `g5p_rv3_recover`,
  `g5p_render_fw_rebuild`). It fails on a planted `writel`/`readl`/`g5p_prepare_kick`/`regmap_write`
  in four of those paths. Declared boundaries: `g5p_render_run()` runs one job exactly as before;
  `g5p_recover()` (core `recover.inc`) is the recovery's deliberate hardware half: the quiesce's
  `g5p_kq_*` ops (audited by `audit_kq.py`: AIC ack, line mask and the PMGR word, no GPU register),
  then the bootstrap's stages on the freshly powered GPU. It runs only with `render_user_recover > 0`.
- `test_render_loss.c`: 117 checks, 0 failures. Every loss input (only a timeout of a non-first job
  with the firmware alive and isolation valid loses just the context), the drain machine (late TA →
  3D published → 3D done → clear; stuck 3D; silence, fault, refused 3D or the bound → terminal), the
  drain bound against the ICD's own wait, and a worker model: labwc's queued jobs are held, never
  refused and never published behind the stuck phase, then run in order.
- `test_render_recover.c`: 56 checks, 0 failures, against the tree's own `dirtystart108.h` and
  `render-user-terminal.h`. The `recover` event (new generation, old proofs void including ALIVE,
  witnessed cycle for the strict origin gate, refused before a release or after a failure, a failed
  cycle fails closed), the claim gate after the second bootstrap (refused until ALIVE in the new
  generation, through the ALIVE event or the late-ALIVE path; closed for good after a failed step),
  two recoveries per boot, the latch reset, and a worker model of the two wedges: with
  `render_user_recover ≥ 1` labwc's held frames all run after the recovery (the first attaches the new
  epoch) and none is refused; with 0, a spent budget, the epoch's first job, or a failed cycle or
  bootstrap, the epoch ends as before. Mutants (dropping the `alive_gen` reset, the `reset_ok_gen`
  set, or the generation step) fail 3, 2 and 9 checks.
- On this tree, the base's gates all pass: tahang1 (10767 checks, 11 mutants caught),
  `test_kexec_quiesce` 107/0, `test_render_bind` 177/0, `test_render_admit` 83/0, `audit_kq.py`
  and `audit_ram_only.py` (the window's pure lookups were added to its allow-list).
- `W=1` build with no warnings. checkpatch `--strict`: 0 errors. The only warnings are the
  `Co-Authored-By` form and MAINTAINERS for new files.

### 11.3 Device plan (orchestrator): order and setup

Two boots, under `caffeinate -i`. `SSH=scripts/omarchy/ssh.sh`.
- **Boot 1** (labwc exactly as deskfold, wl_shm only): B, T5, T3, T4, then F (fault isolation).
- **Boot 2** (labwc with linux-dmabuf): B2, T2 (GPU Wayland client, root then non-root), then R (the
  opt-in recovery soak), always last.

Boot 2 is a fresh boot, reached by warm kexec (`td-kexec`, deskfold step 3) only if Boot 1 printed
no `terminal`, `drain dead` or `refused`; otherwise DFU or cold boot (STATUS rule). F loses a context
but must not end the epoch, so a passing F does not block the hop.

The kernel default is the narrowed window (`render_user_fw_window=1`), but the window has no device
evidence yet, and a page it lacks would fault labwc at its first frame. That is device-terminal (§4.7)
and ends the live session. So Boot 1 starts labwc under the kill switch (mode 0, the proven deskfold
mapping) and switches new contexts to the window after B: T5's first run r1 is then the window test
(the "W" step folds into it), and T4 repeats it as non-root. The mode is latched at context creation,
so labwc keeps mode 0 whatever the fixtures do. Boot 2 uses the window for labwc too only if Boot 1's
window runs all passed (see B2). After any `terminal kind=timeout|fault`: stop, keep the boot for the
log, and do not warm-kexec out of a faulted boot (STATUS rule).

The kill-switch copy of the boot script (one extra write before labwc's context exists):
```
python3 - <<'EOF'
from pathlib import Path
s = Path("/tmp/desk/boot-desk.sh").read_text()
a = '"echo 0 > $P/boot_hb116_seed"'
assert s.count(a) == 1
s = s.replace(a, a + "$'\\n'" + '"echo 0 > $P/render_user_fw_window"')
Path("/tmp/desk/boot-desk-fullwin.sh").write_text(s)
EOF
chmod +x /tmp/desk/boot-desk-fullwin.sh; grep -c render_user_fw_window /tmp/desk/boot-desk-fullwin.sh   # 1
```

**B. Boot.**
```
UPLOADFIX=1 /tmp/desk/boot-desk-fullwin.sh build/boot/m1n1-linux-multiclient2-995d684abd.bin multiclient2-995d684abd
$SSH 'uname -r; P=/sys/module/a9_gpu_drm/parameters; for p in render_user_fw_window render_pinned_mib kexec_quiesce prime_experiment render_user_drain_ms render_user_recover render_user_test_stall_job render_user_hard_ms; do printf "%s=%s " $p "$(sudo -n cat $P/$p)"; done; echo'
$SSH 'sudo -n dmesg | grep -E "RENDER-V3 (fw-window|terminal|refused|bind)|RENDER-USER attached|G5P VM release"'
$SSH 'sudo -n grep "LABWC-A9 FRAME" /run/desk/labwc.log | tail -1; ls -l /dev/dri/renderD128; id'
```
PASS:
- the release is `7.3.0-rc1-g995d684abd67`, and the parameters read
  `render_user_fw_window=0 render_pinned_mib=1024 kexec_quiesce=2 prime_experiment=Y
  render_user_drain_ms=45000 render_user_recover=0 render_user_test_stall_job=0 render_user_hard_ms=60000`
  (hard 60000 is the latency101 tuple; it makes the 45000 drain valid, so isolation is on);
- labwc's `RENDER-USER attached TA=8 3D=7 … firmware DC3`, with no `fw-window` line yet (mode 0
  builds none);
- labwc's FRAME `n` advances (touch the screen), with no `terminal`.

Record the render node's mode and group, and `id`, for T4. Then, as root, switch new contexts to the
window: `$SSH 'echo 1 | sudo -n tee /sys/module/a9_gpu_drm/parameters/render_user_fw_window'`.
labwc's context keeps mode 0. Every fixture context below (T5, T3, W, T4) is created in mode 1, so
the first one, r1, already exercises the window.

**Fixture setup** (the latency101 fixture with its readback oracle, as in §9):
```
BIN=build/gpu/latency101/mesa-a9-frames/bin/a9-first-frames.aarch64   # sha256 58fd788c…
$SSH 'mkdir -p /tmp/mc2 && chmod 0777 /tmp/mc2 && cat > /tmp/mc2/a9-first-frames && chmod 0755 /tmp/mc2/a9-first-frames' < "$BIN"
run() { $SSH "cd /tmp/mc2 && sudo -n timeout -s KILL 400 ./a9-first-frames --node /dev/dri/renderD128 --abi v3 --timeout-ms 20000 --frames 10 --out /tmp/mc2/$1.bgra --report /tmp/mc2/$1.txt >/tmp/mc2/$1.out 2>&1; echo $1 rc=\$?"; }
mark() { $SSH "sudo -n dmesg | grep -E 'RENDER-V3 (bind|unbind|close|terminal|refused)|G5P VM release' | tail -${1:-4}; grep MemAvailable /proc/meminfo"; }
mark 2 | tee /tmp/desk/mc2-baseline.txt
```

**T5. Slot reuse is safe.** Each fixture run binds, renders 10 frames, exits, and is unbound. The
next run gets the same pair, with a new catalogue, new render targets and new BOs at the same VAs:
```
run r1; mark; run r2; mark; run r3; mark
```
PASS, for each run:
- `rc=0`: every frame MATCHes the readback oracle. The fixture prefills its own target before
  every frame, so a job that walked the previous owner's stale translations (PC/PD/PT cache or SLC
  under a reused context ID) would leave the prefill and MISMATCH.
- `RENDER-V3 bind slots=10/9 ret=0 … cached=<T>/<T> … pc=<pa>`, with a `pc=` that differs from the
  previous run's (a new catalogue on the same slots).
- after the exit, `RENDER-V3 unbind why=close slots=2 verified=2 ret=0 consumed_us=<N> dc=<S>-><S+2>/<S+2> bad=0 first=10 last=9`,
  then `RENDER-V3 close slots=10/9 plan=unbind why=0 ret=0 … retained=0`, then
  `G5P VM release maps=<m> … device_pinned=<D0>K`, where D0 is the same every time (labwc's own
  pinned memory).

Record every `consumed_us`: it is the publish-to-consume time of a two-record unbind batch after
retirement, with no doorbell. A run whose bind reads `slots=12/11` means the previous unbind did
not verify. Read its `close … plan=retain why=<bits>` (`core/render-user-close.h`) and stop T5.

**T3. 20 client start/stop cycles, memory back to the baseline.**
```
for i in $(seq 1 20); do run c$i; done; mark 61 > /tmp/desk/mc2-t3.txt; tail -3 /tmp/desk/mc2-t3.txt
$SSH 'cd /tmp/mc2 && for n in p1 p2; do (sudo -n timeout -s KILL 600 ./a9-first-frames --node /dev/dri/renderD128 --abi v3 --timeout-ms 20000 --frames 10 --out /tmp/mc2/$n.bgra --report /tmp/mc2/$n.txt >/tmp/mc2/$n.out 2>&1; echo $n rc=$?) & done; wait'
mark 8; run c21; mark 4
$SSH 'sudo -n grep "LABWC-A9 FRAME" /run/desk/labwc.log | tail -1'
```
PASS:
- all 20 `rc=0`, each with its bind on `10/9`, its `unbind why=close … verified=2 ret=0` and
  `close … plan=unbind`;
- after c20, `device_pinned` equals D0 and MemAvailable is within 32 MiB of the baseline;
- the concurrent pair binds `10/9` and `12/11` (either order) and both unbind, and c21 then binds
  `10/9` again;
- no `plan=retain`, `terminal`, `refused` or `3d-refused`;
- labwc's FRAME `n` advanced over the whole run (labwc and fixture jobs interleave, round-robin).

**T4. A non-root client.** The SSH user `linux` (not root, no `sudo`) runs the same fixture:
```
$SSH 'ls -l /dev/dri/renderD128; id; cd /tmp/mc2 && timeout -s KILL 400 ./a9-first-frames --node /dev/dri/renderD128 --abi v3 --timeout-ms 20000 --frames 10 --out /tmp/mc2/u1.bgra --report /tmp/mc2/u1.txt >/tmp/mc2/u1.out 2>&1; echo u1 rc=$?; grep -h "caps=" /tmp/mc2/u1.txt; tail -3 /tmp/mc2/u1.out'
mark 4
```
If the render node is not accessible to `linux` (mode 0660, group `render`, not in it), first
`$SSH 'sudo -n usermod -aG render linux'` and reconnect. PASS:
- `u1 rc=0`, and `caps=` has bits 27 and 28 (`0x18000000`);
- its bind on `10/9`, `unbind why=close … ret=0`, `close … plan=unbind`, no `terminal kind=fault`.
  A fault would name a page the window lacks: record it.

As root, `echo 0 | sudo -n tee /sys/module/a9_gpu_drm/parameters/render_user_fw_window`, then rerun
as `linux`. Expect `u2 rc≠0`, with the fixture reporting `CREATE_CONTEXT` `EPERM`: the kill switch
needs `CAP_SYS_RAWIO`. Then `echo 1 | sudo -n tee …/render_user_fw_window` again. labwc's context is
unaffected: the mode is latched at creation.

**F. Fault isolation: a client job is lost, labwc survives (Boot 1, last).** The test hook makes the
host give up on one fixture job's TA after 1 ms while the firmware completes it, which is exactly
the host's path for a stuck job with the firmware alive (`render_user_test_stall_job`, latched at
context creation, so labwc's context is not affected). The owner keeps touching the screen during
f1, so labwc's frames queue behind the lost job.
```
P=/sys/module/a9_gpu_drm/parameters
$SSH "echo 3 | sudo -n tee $P/render_user_test_stall_job"
run f1
$SSH "echo 0 | sudo -n tee $P/render_user_test_stall_job"
$SSH 'sudo -n dmesg | grep -E "RENDER-V3 (test-stall|lost|drained|drain dead|terminal|refused|close|unbind)|G5P VM release" | tail -12' | tee /tmp/desk/mc2-f.txt
$SSH 'sudo -n grep "LABWC-A9 FRAME" /run/desk/labwc.log | tail -1; tail -5 /tmp/mc2/f1.out'
run f2; mark 4
$SSH 'sleep 30; sudo -n grep "LABWC-A9 FRAME" /run/desk/labwc.log | tail -1'
```
PASS:
- `RENDER-V3 test-stall seq=S job=3 slots=10/9: TA bound 1 ms`, then
  `RENDER-V3 lost seq=S engine=0 slots=10/9 phase_ms=… drain_ms=45000: context lost, device kept,
  channel blocked`, then `RENDER-V3 drained seq=S engine=0 slots=10/9 lost_ms=<L>: channel clear, held
  jobs resume`, with L of a few seconds at most (the 2 s observation window plus the late TA and its
  3D);
- f1 `rc≠0`: the frames before the context's 3rd job MATCH, the frame of that job fails
  (`ETIMEDOUT`), later ones fail `EIO` or are not submitted;
- no `terminal`, `drain dead` or `refused` line; labwc's FRAME `n` keeps advancing after the drain,
  and 30 s later;
- f1's exit: `close slots=10/9 plan=unbind … retained=0` (a drained lost context may be unbound) and
  its `G5P VM release`; f2 then `rc=0` with `bind slots=10/9 ret=0`, `device_pinned` back to D0.

If `test-stall` is not followed by `lost`, the TA beat 1 ms: rerun with the parameter at 4.
A `drain dead` line means the late TA or 3D did not complete: the epoch ends as before (labwc stops
rendering). Record the line and the `RENDER-V3 lost` before it, keep the boot, and do not hop.

**B2. Boot 2: labwc with linux-dmabuf.** Only after Boot 1 passed T5 and T4 on the window, use
`boot-desk.sh` (labwc on the window); otherwise derive from the kill-switch copy as below. The
variant drops `LABWC_A9_NO_LINUX_DMABUF` from the staged session environment before labwc starts:
```
python3 - <<'EOF'
from pathlib import Path
s = Path("/tmp/desk/boot-desk-fullwin.sh").read_text()   # or boot-desk.sh, see above
a = 'echo "== launch Labwc"'
assert s.count(a) == 1
add = r'''$SSH "sed -i '/^LABWC_A9_NO_LINUX_DMABUF=/d' $R/a9-mesa/share/labwc119/gpu.env" && echo "linux-dmabuf on"'''
Path("/tmp/desk/boot-desk-dmabuf.sh").write_text(s.replace(a, add + "\n" + a))
EOF
chmod +x /tmp/desk/boot-desk-dmabuf.sh; grep -c NO_LINUX_DMABUF /tmp/desk/boot-desk-dmabuf.sh   # 1
$SSH 'sudo -n systemctl stop desk-window.service desk-read.service desk-labwc.service; sleep 1'
$SSH 'echo td-kexec | sudo -n tee /sys/bus/platform/devices/201000000.gpu/command; sudo -n dmesg | grep -E "GPU-KQ|RENDER-V3 unbind|POWER gfx-off|command td-kexec"' | tee /tmp/desk/mc2-hop-kq.txt
UPLOADFIX=1 /tmp/desk/boot-desk-dmabuf.sh build/boot/m1n1-linux-multiclient2-995d684abd.bin multiclient2-995d684abd
$SSH 'sudo -n grep -cE "LINUX-DMABUF disabled" /run/desk/labwc.log; sudo -n grep -iE "dmabuf|dma-buf|drm_format|import" /run/desk/labwc.log | head; sudo -n grep "LABWC-A9 FRAME" /run/desk/labwc.log | tail -1; grep -c NO_LINUX_DMABUF /run/desk/gpu.env'
```
PASS before the jump as deskfold step 3 (`unbind why=kexec … ret=0`, `GPU-KQ … plan=detach+cycle`,
`POWER gfx-off … off_ret=0 on_ret=0`). After: `linux-dmabuf on` printed by the script, the
`LINUX-DMABUF disabled` count 0 and no `NO_LINUX_DMABUF` in `/run/desk/gpu.env`, labwc attached, the
desk-window probe presented, FRAME `n` advancing on touch. If labwc fails to start or logs a
linux-dmabuf error with no GPU client running, that is R7 in labwc/wlroots: record labwc.log, relaunch
with plain `boot-desk.sh` and skip T2.

**T2. A GPU Wayland client composited by labwc (Boot 2).**
```
$SSH 'mkdir -p /tmp/mc2 && chmod 0777 /tmp/mc2'
$SSH 'cat > /tmp/mc2/client-gpu.env' < configs/labwc-a9/client-gpu.env
$SSH 'cat > /tmp/mc2/gpu-client.sh && chmod 0755 /tmp/mc2/gpu-client.sh' < scripts/gpu/multiclient2/gpu-client.sh
$SSH 'for c in es2gears_wayland glmark2-es2-wayland weston-simple-egl vkcube; do printf "%s=%s " $c "$(command -v $c || echo none)"; done; echo'
```
Use the first available of `es2gears_wayland`, `weston-simple-egl`, `glmark2-es2-wayland` as `CLIENT`
(es2gears with `-info`). If none is installed, install `mesa-demos` with pacman (needs the network;
orchestrator's call) or skip T2. `gpu-client.sh` runs the client with `env -i` and only the lines of
`client-gpu.env`, the staged Mesa of the desk session (`/tmp/mesa102-desk/a9-mesa`), and labwc's
socket.

T2a (root):
```
mark 2 | tee /tmp/desk/mc2-t2-base.txt
$SSH 'sudo -n systemd-run --unit=mc2-t2a /tmp/mc2/gpu-client.sh root 60 es2gears_wayland -info'
$SSH 'sleep 20; for i in 1 2; do sudo -n dd if=/dev/fb0 of=/tmp/mc2/t2a-$i.raw bs=12582912 count=1 status=none; sleep 1; done; cmp -s /tmp/mc2/t2a-1.raw /tmp/mc2/t2a-2.raw; echo fb_differs=$?'
$SSH 'sleep 45; sudo -n grep -E "GL_RENDERER|GL_VERSION|client rc|rror" /run/desk/client-gpu.log | tail -6'
mark 8
$SSH 'sudo -n dmesg | grep -cE "RENDER-V3 end seq=[0-9]+ ret=-"; sudo -n grep "LABWC-A9 FRAME" /run/desk/labwc.log | tail -1; sudo -n grep -iE "dmabuf|import" /run/desk/labwc.log | tail -5'
```
PASS:
- `GL_RENDERER` names Zink on the g5p device (`Apple A9 G5P`), not llvmpipe or softpipe;
- the client's context binds (`RENDER-V3 bind slots=10/9 ret=0`, or the lowest free pair), and the
  `ret=-` count is 0;
- `fb_differs=1` (the two frames a second apart differ: the gears move and labwc composites them);
  keep both raw dumps;
- `client rc=124` (ended by the 60 s timeout; any other nonzero rc is a client failure: record its log);
- after the exit: the client's `close … plan=unbind`, its `G5P VM release`, labwc's FRAME still
  advancing on touch, and no labwc import error.

T2b (the same client as the non-root user `linux`, in group `render` after T4):
`sudo -n systemd-run --unit=mc2-t2b /tmp/mc2/gpu-client.sh linux 60 es2gears_wayland -info`, the same
reads, the same PASS. The script grants `linux` labwc's socket only (ACLs) and its own runtime dir.

T2c (20 client start/stop cycles, memory back to baseline):
```
$SSH 'for i in $(seq 1 20); do sudo -n /tmp/mc2/gpu-client.sh root 8 es2gears_wayland >/dev/null; done; sudo -n grep -c "client rc=124" /run/desk/client-gpu.log'
mark 41 > /tmp/desk/mc2-t2c.txt; tail -3 /tmp/desk/mc2-t2c.txt
```
PASS: the count is 22 (T2a, T2b and the 20 cycles); 20 `close … plan=unbind` and 20 `G5P VM release`; after the last,
`device_pinned` equals the base value of `mc2-t2-base.txt` (labwc's own, its imports of the client's
buffers released through UNMAP beside its queued jobs) and MemAvailable within 32 MiB of it; no
`plan=retain`, `terminal` or `refused`.

**R. Opt-in in-boot recovery (Boot 2, always last; may cost the boot).** Only if everything before
passed. A firmware-silent wedge cannot be produced on demand; the two seen came from vkcube and the
XFB hold job under touch load. Turn recovery on and run the closest mix for 15 minutes:
```
$SSH "echo 1 | sudo -n tee $P/render_user_recover"
$SSH 'sudo -n systemd-run --unit=mc2-r /tmp/mc2/gpu-client.sh root 900 vkcube --wsi wayland'   # else es2gears_wayland
# the owner drags and taps throughout; then:
$SSH 'sudo -n dmesg | grep -E "RENDER-V3 (lost|recovered|recover failed|drain dead|terminal)|GPU-RECOVER|DS108 gen=[2-9]|RENDER-USER attached"; sudo -n grep "LABWC-A9 FRAME" /run/desk/labwc.log | tail -1'
$SSH "echo 0 | sudo -n tee $P/render_user_recover"
```
Results:
- No wedge in 15 minutes: R not exercised. Record it; that is not a failure.
- PASS if a wedge happened: `RENDER-V3 lost seq=… why=ta-silent|3d-silent|…-fault: context lost,
  device recovers`, then `GPU-RECOVER why=lost-job ret=0 stage=done ms=<M> gen=2 end=recover`, the
  `DS108 gen=2` lines (recover, wake, maps, load, release, alive, claim) all with `failed=0`,
  `RENDER-V3 recovered n=1 contexts=<C> unbound=<U>`, labwc's next job attaching the new epoch
  (`RENDER-USER attached …`), and FRAME `n` advancing again. Record M.
- FAIL: `GPU-RECOVER … ret≠0` or `RENDER-V3 recover failed` (the device is failed and labwc stops), or
  a hang. Collect dmesg (pstore after a hang), then DFU or cold boot; never warm-kexec out of it.

**Close.** Every parameter back to its default (`render_user_test_stall_job=0`,
`render_user_recover=0`); copy `/tmp/mc2/*.txt`, `*.out`, the T2 raw dumps and the dmesg greps to
`docs/gpu/experiments/logs/multiclient2/`.
