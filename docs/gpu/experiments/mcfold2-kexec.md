# mcfold2: warm kexec out of a multi-client boot

2026-11-01. Kernel branch `a9/gpu-mcfold2` (worktree `/src/wt/mcfold2`), one commit on mcfold1
(`85ad2697fd`):

- `a2ad7938e6` drm/apple/g5p: quiesce the GPU before a kexec or reboot

Payload `build/boot/m1n1-linux-mcfold2-a2ad7938e6.bin`, sha256
`e4c5b441a09fdd9668a02d3cdce56443ea82dc297e34b978b188cc670d0f5d25`. Entry `mcfold2-a2ad7938e6`
(`build/gpu/mcfold2/kernel/mcfold2-a2ad7938e6-entry/`, `Image.gz` `e892696b…`, per-file sha256 in
`build/gpu/mcfold2/kernel/entry.sha256`). Release `7.3.0-rc1-ga2ad7938e690`. Config byte-identical to
`build/kernel-gpucombined118/config`. The cmdline is mcfold1's except `linux_a9.entry`. The DTB is
identical to mcfold1's, so `linux-a9-kexec` reuses the running FDT. Built by
`scripts/gpu/mcfold2-build.sh verify|package|check`. Not run on hardware.

## 1. What happened, and the mechanism

Line numbers refer to the mcfold1 base unless marked `(new)`.

**The outgoing kernel does nothing to the GPU at kexec.**
- `teardown` defaults to 0 (`drivers/gpu/apple-g5p/teardown.inc:26`), and the reboot notifier
  returns at once when it is 0 (`teardown.inc:425`).
- `shutdown_park` defaults to false (`lifecycle.inc:712`). `g5p_shutdown()` returns before doing
  anything (`lifecycle.inc:731`).
- `scripts/gpu/latency101-tuple.txt` sets neither, and `/tmp/desk/boot-desk.sh` doesn't either.

So on every warm kexec, single-client or not, the firmware keeps running from the outgoing kernel's
RAM across the relocation. The incoming kernel stops it only with its PMGR GFX cycle
(`apple_pmgr.kexec_gpu_cycle=1`, `drivers/pmdomain/apple/pmgr-pwrstate.c:168-200`), at 0.39 s in
`logs/multiclient1/dmesg-full.txt:2506`. `gpu-warm-kexec-hang-proof.md` §0.3 already noted this
("the firmware keeps running and keeps writing the event ring across relocation").

**What the firmware still references at that moment:**

| | single client (works) | T1 boot e7ac4138 (died) |
|---|---|---|
| firmware rings, image, kernel context | yes | yes, same rates: HB116 idle `irqs=478 events=239 aux=1912`, as on latency123 boot 1 |
| user slots with a live CBASE and cached tag | 2 (8/7) | 12 (7..18), PCs at `0x84c6b8000..0x84d630000` (`logs/multiclient1/mc1-dmesg.txt` bind lines) |
| GPU-reachable RAM that is never freed | labwc's VM | about 900 MiB more: five more VMs, their RTs and pinned BOs (`g5p_render_retain()`, `drm-render.inc:335`; tables from `dma_alloc_coherent`, `drm-vm.inc:124`) |

**The candidates:**
- **(a) "Only the first render owner is torn down": wrong in detail.** Nothing is torn down, not
  even the first claimant. A bound TYPED_V3 context is retained until reboot
  (`drm-render.inc:1413`), and nothing at shutdown unbinds it. Zero of six pairs were detached.
- **(b) Firmware and GPU state keeps pointing into RAM the next kernel reuses: the remaining
  explanation.** Two things hold for every warm kexec:
  - the firmware writes into old RAM until 0.39 s;
  - the GPU MMU keeps a CBASE for every bound slot into page tables that the new kernel is free
    to reallocate. When it reuses them is not known.

  The multi-client boot had 6× the live contexts and about 900 MiB more retained memory. Its idle
  firmware traffic was the same. The code does not show which exact GPU write hit the new kernel.
  Every write path that needs a live firmware, or a CBASE into old tables, ends at the outgoing
  kernel now (§2).
- **(c) The new kernel's GPU probe blocks before netconsole: refuted.**
  - The incoming GPU probe touches no GPU. It ends at "awaiting explicit command"
    (`core.inc:695`), and `g5p_ds_hook_probe()` reads only `/chosen` (`dirtystart108.inc:85`).
  - The firmware bootstrap is host-triggered after SSH (`boot-desk.sh:25-28`), so it can't block
    the boot.
  - The only early GPU-related step is the PMGR cycle, and its poll is bounded:
    `APPLE_PMGR_PS_SET_TIMEOUT` = 100 µs, `pmgr-pwrstate.c:40,135`.
  - If the cycle fails, the gfx power-domain probe fails, the GPU never probes, and the boot goes
    on.
- **(d) Why there was no trace.**
  - A new kernel's first netconsole line arrives only at about 4–6 s, once the initramfs brings up
    the USB gadget (`logs/multiclient1/netconsole.log:2`, `[4.346689]`). The first 4 s are dark.
  - The outgoing kernel's last netconsole line is always the `sd-remount` EXT4 line, on the good
    hops too (`logs/latency123/boot1-netconsole.log:1`). Nothing from `kernel_kexec()` or
    `device_shutdown()` ever reaches netconsole.
  - What does survive is the ramoops console. It is registered at 0.06 s, in a 2 MiB region at
    `0x87b800000` that is carried in the kexec FDT. `linux-a9-pstore --read` copies it to
    `/run/linux-a9/pstore/` on the next boot, then archives it to
    `/var/lib/linux-a9/pstore/<boot_id>/`. A DFU may or may not keep it.

The latency123 boot 3 hop died with the same signature, from a wedged TA job (arm A). The labwc
freeze being fixed by tahang1 (a TA-wait timeout while the firmware is healthy) produces the same
kind of epoch: the TYPED_V3 latch is `failed`. The quiesce handles both (§2, plan rows "wedged").

## 2. The fix

### Source side: one quiesce, run from the reboot notifier

`g5p_kq_run()` (`teardown.inc`, new) runs once per boot, whatever `teardown` is set to. It is called
from three places:
- the reboot notifier at `INT_MAX`, before `device_shutdown()`, for kexec, reboot, halt and
  power-off;
- `.shutdown`, as a second chance;
- the new sysfs command `td-kexec`, which runs the same quiesce by hand.

The order and the plan are pure functions in `render-user-quiesce.h` (new):

1. **Admission.** Close the r8 admission word and wait up to 20 ms for submitters inside it. Jobs
   queued before the close fail with `-ESHUTDOWN` before their input wait
   (`g5p_render_run()`, new lines).
2. **Lock.** Bounded trylock of the command lock (2 s, 5 ms steps). If we hold it, no job or
   command is in flight.
3. **Detach.** Every user slot the firmware may hold is unbound in one RAM-only DC3 batch.
   - "Held" means a slot in 7..255 with a non-zero PA (`0x86`) or host tag (`0x84`). The firmware's
     own arrays are the authority, so labwc's 7/8, every fixture pair and any spares are all
     covered, without walking contexts.
   - Batch: PA and tag go to 0, then one `{3, slot, 0, 0, 0}` record per slot, then one producer
     store with release semantics. The wait is the bind's RAM-only `g5p_rv3_dc_wait()` (3 s), and
     each slot is checked afterwards (`g5p_ru_unbind_check()`: record intact, PA, host and
     **cached** tag all 0; only the firmware's DC3 handler writes the cached tag).
   - The detach runs only if all of these hold: we hold the lock, the firmware is not failed, the
     TYPED_V3 latch noted no timeout or fault, the DC ring is idle, and at least one slot is held.
     The RE says a detach needs every use retired first (README round 15 §1).
4. **Freeze and IRQ.** `stopped`, `alive=0`, r73b watchdog disarmed, r96 quiet latch (the
   teardown ladder's freeze). Then the AIC software-IRQ ack, `disable_irq_nosync()` and
   `synchronize_irq()`.
5. **Cycle.** One PMGR GFX gate cycle through `g5p_r33_gfx_off(…, 10 ms)`. It is bounded, refuses a
   gate that is not plainly on, and always ends with the gate on. It runs only if no host thread can
   be inside a GPU register access: either we hold the lock, or the holder is the TYPED_V3 executor
   (`rdev->executing`, new) after the latch went quiet, with `render_user_wake` off.

It leaves the GPU powered, with its firmware stopped and in reset, and the boot state frozen. The
next kernel then finds the gate on (`kexec GFX power cycle start: 000003ff`), the same state every
GPU-active warm hop leaves today, and cycles it again. One line reports each run:

```
GPU-KQ why=<reboot-notifier|shutdown|sysfs> mode=2 inside=0 locked=1 booted=1 failed=0 wedged=0 ram_only=1 exec=0 dc=<c>/<p> held=<N> plan=detach+cycle skip=0 detach=0 froze=1 cycle=0 ms=<t> end=kq
RENDER-V3 unbind why=kexec slots=<N> verified=<N> ret=0 consumed_us=<us> dc=<S>-><S+N>/<S+N> bad=0 first=7 last=<s> mode=ram-dc3 end=v3
POWER gfx-off ms=10 held_us=<us> before=000003ff end=<..0..> after=000003ff off_ret=0 on_ret=0
```

The plan table, as checked by the host test:

| state | detach | cycle | `skip` bits |
|---|---|---|---|
| clean, any number of contexts | yes | yes | 0 |
| a job in flight, holder is the RAM-only V3 executor | no | yes | BUSY |
| holder unknown (a sysfs command, or job 1 before its TA completes) | no | no | BUSY, HOLDER |
| wedged: V3 timeout or fault noted (arm A, the labwc TA-wait race), lock ours or held by the timed-out job | no | yes | WEDGED (+BUSY) |
| fault event, DC ring busy (pending bind), nothing held, no DRM frontend | no | yes | FAILED, RING, NONE, NO_DRM |
| `kexec_quiesce=1` | yes | no | RAM |
| `kexec_quiesce=0` | no | no | OFF (no freeze either) |
| firmware never released on this boot | no | no | DOWN (nothing else runs) |

Worst case it takes 20 ms + 2 s + 3 s + about 30 ms. `kexec_quiesce` (0600, default 2) is the
kill switch if the cycle is ever suspected: `echo 1 > /sys/module/a9_gpu_drm/parameters/kexec_quiesce`.

On a cold-booted (m1n1) kernel, the pmgr driver leaves AUTO set on the gate
(`apple_pmgr_ps_init_state()` when not warm). `g5p_r33_gfx_cyclable()` then refuses, and the
quiesce logs `POWER gfx-off refused …` and `cycle=-16`. The detach still runs, and that hop relies
on the incoming cycle as before. That is expected, not a failure. Every hop in §4 starts from a
warm boot, where the gate reads `000003ff` and the cycle runs.

### The shared unbind helper (PART 2 reuses it)

`g5p_rv3_unbind_slots(d, slots, n, why)` (`drm-render-unbind.inc`, new) is RAM-only. It needs the
core lock (one writer on the DC ring) and a caller that has retired every use of the slots. Stage
2's per-context unbind calls it with one context's pair, from a `render_wq` item after
`queued == 0`.

**Per slot after `ret=0`:**
- PA 0, host tag 0, cached tag 0;
- CBASE 0 and the PC level invalidated (by the firmware's DC3 handler);
- nothing else changes. The slot's selector bytes are rewritten at every dispatch, and PM state is
  device-wide.

**The caller then frees the translations and returns the slots.** Stage 2 still needs next-fit
allocation so a slot is not reused at once. That also stops a late unbind from clearing a new
owner's PA (README "gate must prevent an old unbind from clearing a new owner's PA").

**On timeout:** `dc_pending` is set, and later binds and unbinds are refused until the consumer
catches up. The PA and tag words are already zero, but the firmware's CBASE is still the old one.
Keep the memory.

### Target side

Nothing in the incoming kernel waits on the GPU before userspace (§1 c):
- the pmgr cycle is bounded and fails closed (the GPU stays unprobed and the boot continues);
- the GPU probe is passive;
- the bootstrap is host-triggered and bounded, and `dirty_start=3` refuses an unwitnessed warm
  origin.

The incoming kernel never sees old PA arrays (fresh shared pages, firmware installs all 256 CBASEs
from them at boot), so "N bound contexts" can't reach it. The only incoming exposure was the
firmware running in [0, 0.39 s), and nothing in an incoming kernel can protect RAM that was reused
before it runs. That is why the fix is on the outgoing side. Panic kexec runs no notifier, so it
still relies on the incoming cycle (as before).

### Diagnosability

No new mechanism is needed. Every quiesce line is `dev_emerg`, so it lands in the outgoing ramoops
console. The next boot keeps it in `/run/linux-a9/pstore/console-ramoops-*`. For a hop that might
fail, run `td-kexec` by hand first (§4): the receipts then come back over SSH before the jump.

## 3. Offline checks (`scripts/gpu/mcfold2-build.sh check`)

- **`scripts/gpu/mcfold2-host/test_kexec_quiesce.c`: 107 checks, 0 failures.** It runs against a
  mock DC3 firmware and covers:
  - the held-slot scan;
  - a 12-slot unbind batch, consumed and verified;
  - a wedged firmware that never consumes: bounded at 3 s, nothing verifies, the old CBASE stays
    live;
  - a partial drain, ring wrap 250→6, a skipped tag copy, a torn record, all 249 slots, and the fit
    rule;
  - every plan row;
  - the call order for a clean run, an in-flight job, a wedged epoch, an unknown holder, a failed
    detach, a refused cycle, modes 0 and 1, and a firmware that was never released;
  - the sequence driving the real unbind for N = 1, 6 and 124 contexts, plus in-flight and wedged
    six-context runs.

  Six source mutants (dropping the lock, holder, wedge, cached-tag or booted guard, or widening the
  fit rule) are all killed.
- **Stage 1 suites on the new headers:** `test_render_bind` 177/0, `test_render_admit` 83/0.
- **Register audit `scripts/gpu/mcfold2-host/audit_kq.py`: PASS.** It walks 32 functions from 14
  roots that must stay RAM-only: no MMIO, no regmap, no GPU window, no bell helper. Two steps touch
  hardware, each limited to one justified access:

  | function | register access | justification |
  |---|---|---|
  | `g5p_kq_irq_off` | `writel(0x4000, swirq+0x94)`, IRQ line mask | the AIC software-source ack the IRQ handler already issues on every interrupt (`boot.inc` `g5p_boot_irq`); not a GPU register |
  | `g5p_kq_cycle` → `g5p_r33_gfx_off` | `regmap` on `pmgr` at `0x80340` only | the same gate cycle the next kernel performs on a live, unquiesced firmware on every GPU-active warm hop (inherited `000003ff` on latency123 boots 1–3), and the one dirty_start mode 3 runs at bootstrap. It runs after the IRQ mask and only with no host thread in GPU MMIO. **New in this lane:** doing it on the outgoing side was graded GUESS (G1) in `gpu-warm-kexec-hang-proof.md` §5 and has not flown. |

  The audit fails on a planted `writel(…->rgx + 0xb00)` in the unbind and on a regmap to another
  PMGR word. The stage 1 `audit_ram_only.py` still passes (28 functions).

  Asynchronous sources that could reach a GPU register during the 10 ms gate-off window (checked by
  hand, since the call-graph walk can't see them):
  - the r73b watchdog hrtimer: disarmed by the freeze, and `boot_r73b_watchdog_ms=0` in the tuple
    anyway;
  - the r80 drain kthread: dormant without `boot_r80_case`, which is 0;
  - HB116 and the IRQ-side banks: they run inside the threaded IRQ, which is masked and
    synchronized first;
  - render jobs, compute jobs (`g5p_job_run`) and sysfs commands: all take the command lock. While
    the gate is off, either the quiesce holds that lock or the RAM-only V3 executor does. Each of
    them sees `stopped` afterwards.
- **Build and style:** the driver builds with `W=1` and no warnings. checkpatch `--strict` gives only
  the `Co-Authored-By` casing and the MAINTAINERS warning for the new header, as in stage 1.

## 4. Device plan (orchestrator)

Set up as for multiclient1 §9 (`SSH=scripts/omarchy/ssh.sh`). Every hop goes through
`/tmp/desk/boot-desk.sh` with `UPLOADFIX=1`; rerender, the lazy arena and no preload are its
defaults. Run the hops under `caffeinate -i`.

**Step 0 (optional, read-only): the failed hop's records.** If the DFU kept the ramoops region, the
cold boot that followed archived the dead kernel's console:
```
$SSH 'sudo -n ls -la /var/lib/linux-a9/pstore/; for d in /var/lib/linux-a9/pstore/70cef2f6*; do sudo -n tail -c 4000 $d/console-ramoops-* 2>/dev/null; done' > /tmp/desk/mcf2-step0.txt
```
Any `[    0.xxx]` lines there from `7.3.0-rc1-g85ad2697fd25` show how far the dead kernel got.

**Hop 1: boot mcfold2 and bind six contexts.**
```
UPLOADFIX=1 /tmp/desk/boot-desk.sh build/boot/m1n1-linux-mcfold2-a2ad7938e6.bin mcfold2-a2ad7938e6
$SSH 'uname -r; cat /sys/module/a9_gpu_drm/parameters/kexec_quiesce'   # 7.3.0-rc1-ga2ad7938e690, 2
```
Then run **T1 exactly as in multi-client-design.md §9**: upload the fixture
(`build/gpu/latency101/mesa-a9-frames/bin/a9-first-frames.aarch64`, sha256 `58fd788c…`), then
`run a1; run a2; run a3`, then a4 and a5 concurrently, then the dmesg grep. PASS as in §9: five
bind lines `10/9 … 18/17 ret=0`, every frame MATCH.

**Hop 2: the quiesce by hand, then warm kexec into mcfold2.** Stop labwc first, so the quiesce
gets the lock (a queued labwc job would take it):
```
$SSH 'sudo -n systemctl stop desk-window.service desk-read.service desk-labwc.service; sleep 1'
$SSH 'echo td-kexec | sudo -n tee /sys/bus/platform/devices/201000000.gpu/command; sudo -n dmesg | grep -E "GPU-KQ|RENDER-V3 unbind|POWER gfx-off|command td-kexec"' | tee /tmp/desk/mcf2-hop2-kq.txt
UPLOADFIX=1 /tmp/desk/boot-desk.sh build/boot/m1n1-linux-mcfold2-a2ad7938e6.bin mcfold2-a2ad7938e6
```
`tee` exits non-zero when the quiesce ran but its detach timed out (`-110`) or failed its check
(`-117`). Judge from the dmesg lines, not from that exit code.

PASS (before the jump):
- `RENDER-V3 unbind why=kexec slots=12 verified=12 ret=0 … dc=<S>-><S+12>/<S+12> bad=0 first=7 last=18`.
  Record `consumed_us`: it measures a 12-record DC batch after retire with no doorbell.
- `GPU-KQ why=sysfs … locked=1 booted=1 failed=0 wedged=0 … held=12 plan=detach+cycle skip=0 detach=0 froze=1 cycle=0`.
- `POWER gfx-off ms=10 … before=000003ff … off_ret=0 on_ret=0`, and SSH still answers afterwards.

PASS (after the jump):
- the new kernel reaches SSH, and `boot-desk.sh` reports its usual bootstrap
  `ds gen=1 mode=3 origin=warm-cycled failed=0`;
- `RENDER-USER attached TA=8 3D=7 … firmware DC3`;
- labwc renders: fbprobe `result=PASS`;
- the old kernel's notifier line `GPU-KQ why=reboot-notifier already ran this boot` appears in
  `$SSH 'sudo -n grep -ahE "GPU-KQ|RENDER-V3 unbind|POWER gfx-off" /run/linux-a9/pstore/console-ramoops-*'`;
- a fresh T1 `run a1` gives rc=0 with `RENDER-V3 bind slots=10/9 ret=0`.

If the jump fails even though all three pre-jump lines passed, then the firmware was quiet and the
GPU was cycled before the relocation. The cause is then not the GPU: collect Step 0 after the DFU.

**Hop 3: automatic path, into a single-client kernel (backward compatibility).** Leave the boot
from Hop 2 as it is, with labwc plus a1 bound (4 held slots), and warm kexec straight into 124j. The
notifier does the quiesce.
```
UPLOADFIX=1 /tmp/desk/boot-desk.sh build/boot/m1n1-linux-desktop124j-c02a081b6f.bin desktop124j-c02a081b6f
$SSH 'sudo -n grep -ahE "GPU-KQ|RENDER-V3 unbind|POWER gfx-off" /run/linux-a9/pstore/console-ramoops-*' | tee /tmp/desk/mcf2-hop3-prev.txt
```
PASS:
- 124j reaches SSH, and its labwc desktop renders as usual;
- the previous console shows `RENDER-V3 unbind why=kexec slots=4 verified=4 ret=0`;
- it also shows `GPU-KQ why=reboot-notifier … locked=1 … held=4 plan=detach+cycle … detach=0 … cycle=0`
  and `POWER gfx-off … off_ret=0 on_ret=0`.

If labwc was still mid-job at the kexec, `locked=0 exec=1 plan=+cycle skip=0x4` is also a pass.
Returning from 124j to mcfold2 is today's single-client hop, unchanged by this lane.

**The arm-A wedge.** The RAM detach alone would not make it kexec-safe, because a timed-out job may
still hold its slots, and the plan skips the detach then. The quiesce still freezes, masks the IRQ
and cycles the gate before the relocation (plan row "wedged"). The same cycle on a wedged GPU was
what let latency123 boot 2 → boot 3 come up. So mcfold2 should make an arm-A boot kexec-safe, but
this is unproven: on boot 3 the incoming cycle was the last GPU step before the death, and we have
no trace of it. Until one arm-A boot has flown with mcfold2, keep the DFU rule from STATUS.md.

To test it with DFU at hand, run `td-kexec` by hand on the wedged boot first:
- `POWER gfx-off … on_ret=0` followed by a live SSH means the cycle survives a wedged GPU. Then do
  the hop.
- A hang at that step means the cycle itself hangs on a wedged GPU, and that needs DFU.

The labwc TA-wait race that tahang1 is fixing produces the same "wedged" plan (cycle, no detach).
