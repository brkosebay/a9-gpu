# faultiso: a client job that stops the GPU loses only its context

Kernel branch `a9/gpu-faultiso` (worktree `/src/wt/gpu-faultiso`) on multiclient2
(`a9/gpu-multiclient2@995d684abd`), three commits, tip **`abd31016e2`**:

| commit | what |
|---|---|
| `9a0faae2e4` | detect a dead firmware from the first second of a phase (silence clock from the publish) |
| `5c4a4d6819` | a client job that stops the firmware loses only its context (errno, ruling window, visible latch; `core/render-user-fault.h`) |
| `abd31016e2` | test hook: one client job faults on the GPU (`render_user_test_fault_job`, default 0, CAP_SYS_RAWIO creator only) |

Built and packaged by `scripts/gpu/faultiso-build.sh verify|package|check`. Payload and entry are
in §6. The config is byte-identical to `build/kernel-gpucombined118/config`. Cmdline, DTB and
initramfs are multiclient2's; only `linux_a9.entry` differs. No device run yet.

## 1. What happened (vpds rung `vk`, 2026-11-01)

Logs: `build/gpu/vpds/device/{run,dmesg}-vk.txt`, `build/gpu/vpds/device/vk-wedge/`.

- **What the job did** (vpds lane): an undersized uniform store, so a vertex task never finished.
  The TA hung. It was **not an MMU fault**: the full dmesg has no `G2 recovery/page-request/
  channel-error` line (event types 4/7/8). The V3 path reads no register, so the BIF fault latches
  (`e150`/`e158`) were never looked at.
- **What the firmware did:** it went silent. `RENDER-V3 slow seq=20041 … ms=1000 … busy_ms=7
  events=1023304`, and 44 s later `RENDER-V3 lat … silent=1/0 soft_seen_ms=45000/-1 …
  events=1023304`. The decoded-event counter, which the ALIVE stream advances about 290 times a
  second, did not move from about 1 s after the TA's publish until the end. The wait loop drains
  the ring itself on every sample, so the ring was empty: the firmware posted nothing at all.
- **How the kernel decided:** `g5p_ru_wait_grace()` only judged silence from the latency101 soft
  bound (45 s). At 45000 ms the silent stream became `fault = … || silent`, so the phase result was
  `G5P_RU_WAIT_FAULT`. That is the `ta=fault/45000` label: a silent stream, **not** a timeout, and
  not a fault record. `err = -EIO`, hence `ret=-5`.
- **Why the device ended:** `g5p_ru_loss()` gives `LOSS_DEVICE` for any result other than a
  timeout, and for a silent stream. With `render_user_recover=0`, `g5p_rv3_recoverable()` is false,
  so `g5p_rv3_terminal(FAULT)` ran. Job 1's TA had already taken the first-writer latch
  (`complete`), so the note only set the sticky `failed`, **without a line**: there is no
  `RENDER-V3 terminal` line in the vk log, and there never could be after job 1. `g5p_render_run()`
  then marked the boot failed (`ret != -ETIMEDOUT`). labwc's next queued job failed before the
  hardware (`RENDER-USER job=20042 seq=0 complete=-5`), so Zink reported `VK_ERROR_DEVICE_LOST`.
  (§11.3's PASS criterion "no `terminal` line" could not see this.)

## 2. Why the host stall hook kept labwc alive and vk did not

`render_user_test_stall_job` (995d684abd) gives up on the TA after 1 ms through the **legacy single
bound** (`g5p_ru_wait_start()`, `soft_ns = 0`). In that mode the silence rule is off, so the result
is `G5P_RU_WAIT_TIMEOUT` and the loss is `LOSS_CONTEXT` (`g5p_rv3_lose()`, `-ETIMEDOUT`, the boot is
not failed). The firmware is alive and completes the job late, so the drain sees the late TA,
publishes its 3D and clears the channel. vk took neither branch of that path:

1. its firmware stopped, so the phase was a silent-stream fault (device), not a timeout (context);
2. even with the firmware alive, a TA that **never** finishes cannot be drained: the drain bound
   (45 s) runs out, `RENDER-V3 drain dead … why=timeout`, and with `render_user_recover=0` the
   epoch ends the same way.

The per-context loss without a restart only covers a job that eventually finishes. A hung engine
or a stopped firmware needs the GPU restarted, which is what iOS does (§3).

## 3. Real MMU faults: firmware and iOS (RE, 2026-11-01)

Sources: firmware-re README lines 857-930 ("Fault recovery"), 1807-1831 and 2238-2275 ("Fault
ownership"), 4383; the shared Ghidra (functions renamed, comments at `6c834a0` and `6c99a00`).

- **Firmware (proven):** the MMU callback (`80009fb0..8000a000`) reads R0+0x130 and writes the
  pending bits to R0+0x138 itself, so the firmware acknowledges the MMU fault (bit 0x200) on its own.
  A host write to 0x138 cannot restart anything. `100039b8 → 10003a90` decodes e150/e158
  (`1001a494`), sets companion+0 = 2 and enters recovery (`1000d180`). The host sees an **event type
  4** (word1 = 0x200 in R18c, logs 424/425) on the normal ring through IRQ 174. The static path
  `1001fda0 → 1000d448 → 1001fb9c` publishes the event and parks in the self-loop `1001fe08`.
  README 4383: the hardware can also stall with **no** event.
- **iOS kext (proven):** `AGXFirmware_drainFirmwareEventRing` (`6c99788`) case 4 sets
  accel+0x1a24 = 2 and calls `IOGPUScheduler_signalHardwareError` (`6c67b4c`). The restart worker
  (`6c65a2c`, "setting channel %d / global restart type") ends in
  `AGXAccelerator_reportFaultAndRestartChannels` (`6c81dac`). Its tail `6c834a0` is unconditional:
  firmware stop (+358), `resetContextAndDependencyState` (`6c83630`), shared-state rebuild (+a8),
  restart epoch++, firmware boot (+360). "Channel" only picks which process is recorded as guilty.
  Every pending command buffer gets error 5 and the guilty one 2 or 3 (`6c67f38`/`6c67ea8`); the
  readings InnocentVictim/Timeout/Hang are inference from public IOGPU names.
  accel+0x1a24: 1 = host timeout, 2 = event 4, 3 = firmware assert, 4 = "CDM Kill timeout"
  (`6c821b4`). All of them end in the same restart.
- **The one lighter path:** event type 8 (ChannelError; +4 = 1 DMKILL, error 0xe on the matching
  buffer; +4 = 0 MEMFAIL, error 0xd). The host then sends device-control command 0xa and waits for
  it to be consumed, with no restart. That is a firmware-initiated job kill, not a page fault, and
  the meaning of 0xa's fields is still inference (`ds-r29-fw-host-requests.md` §2.2/2.5). Not
  implemented here; a candidate follow-up.

**Verdict:** for a page fault, a hung engine or a stopped firmware there is no per-context ACK,
channel reset or slot unbind that lets the old firmware go on. Continuing needs a GPU restart, so
these cases are wired to the existing in-boot recovery (`render_user_recover`, f64d32b9a3: freeze,
IRQ mask, PMGR gfx gate cycle, second bootstrap, claim gate in the new generation). Unlike iOS, the
innocent contexts' jobs are not failed: they are held and run in the new epoch (Asahi's guilty/
innocent split).

## 4. What changed

- **Silence clock from the publish** (`render-user-wait.h`, 9a0faae2e4). The rule is unchanged
  (no decoded event for 1 s while a phase is pending = dead firmware). It now applies from the
  phase's publish, not from the soft bound. Evidence that a live firmware never goes 1 s without an
  event: latency101 measured 287 events/s through every stall of 0.55-33 s, and the longest gap in
  any poller run was 3.6 ms. The ring is drained before every sample, so a host that slept longer
  than the window catches up and is not silent. With latency101 off the rule stays off, so the
  stall hook is unchanged. Effect on vk: the job is decided at about **2 s instead of 45 s**.
- **The lost job and the ruling window** (5c4a4d6819, `render-user-fault.h`):
  - `g5p_ru_lost_errno()`: a job the recovery takes over reports its own errno (-EIO for a fault
    event or a silence, -EPROTO, the drain's error), and only a timeout reports -ETIMEDOUT.
  - `g5p_ru_job_fails_device()`: a lost job never marks the device failed (the executor ruled).
  - `g5p_ru_ruling()` / `g5p_rdev_ruling()`: a fault event marks the boot failed in the IRQ thread
    before the executor has ruled. While a job or drain runs, or a recovery is pending, another
    client's submit is accepted and held (instead of -EIO), its VM_BIND gets -EBUSY (instead of
    -EIO), and its context status is not LOST. The executor sets `recovering` before it releases
    `executing`; the drain now releases `executing` after its ruling (the old order could show -EIO
    in the gap).
  - The latch: a timeout or fault after job 1's TA complete now prints `RENDER-V3 terminal
    kind=<k> why=<w> latched=complete failed=1 admission=closed` once.
- **Test hook** (abd31016e2): `render_user_test_fault_job=N` (0600), latched at context creation
  for a CAP_SYS_RAWIO creator. The context reserves the 16 KiB page at `0x3fc0000000` (the last GiB
  below the 2^38 VA limit) as a drm_mm node, so it can never be mapped. Its N-th job (never the
  epoch's first) gets TA W+0x98, the VDM control stream, pointed at that page. This is a RAM edit of
  the job's own W copy before it is staged; the validator is unchanged. The log line is `RENDER-V3
  test-fault seq=… job=N slots=…/… va=0x3fc0000000: TA control stream unmapped`. A VM that already
  uses the page logs `test-fault off` and runs normally.
- **Not changed:**
  - `render_user_recover` stays 0 by default: flipping it is the owner's call after §6 passes
    (STATUS: "once labwc re-attaches, turn recovery on by default").
  - A job that stops the firmware with recovery off still ends the epoch, but now visibly.
  - The epoch's first job is always device-terminal.
  - A stuck engine with the firmware alive still takes hard 60 s + drain 45 s before the recovery.
    That is inside the ICD's 125 s wait, but labwc is frozen for it.
  - Event type 8 has no 0xa handling.

**Real MMU faults with this branch:** event 4 (or 7/8) is decoded, the boot is marked failed, and
the next sample (about 1-2 ms) decides `WAIT_FAULT`. That gives `LOSS_DEVICE`; with
`render_user_recover ≥ 1` the context is lost and the GPU recovered, and the ruling window keeps
labwc's submits and binds from failing in between. A fault that parks silently is caught by the
silence clock in about 1 s. A fault while no job runs (no job to blame) still fails the device.
vk was a hang, not a fault, so the hook in §6 is the first real MMU fault to be run on purpose.

## 5. Offline checks (`faultiso-build.sh check`, all PASS on abd31016e2)

- `scripts/gpu/faultiso-host/test_fault_iso.c`: **160 checks, 0 failures**.
  - Compiled against the tree's own `render-user-{wait,loss,fault,terminal,serial}.h`.
  - Covers the three fault signatures (vk silence decided at about 2 s; event 4 at the next sample;
    a stuck engine with the firmware alive takes the hard bound, then the drain) and the errno and
    device-failure rules (exhaustive).
  - The ruling window, including an interleaving model in which the executor's order is never seen
    as "neither", while the old drain order is.
  - The latch's line, printed exactly once.
  - The hook's trigger: one job per context, never the epoch's first.
  - A worker model: labwc and a client whose 3rd job faults. Under all three signatures labwc's
    frames are never refused, it attaches twice (boot, new epoch), the client's jobs 4-5 fail -EIO
    without hardware, and a new client runs. With recovery 0 it replays the vk outcome.
- `mutants.py`: **5/5** header mutants caught (errno always -ETIMEDOUT, a lost job failing the
  device, the ruling ignoring `recovering`, the hook on the epoch's first job, silence only from the
  soft bound).
- `patches/gpu-latency/host/test_latency101.c`: **715/0** on this tree. F1/F6 are switched by
  `G5P_RU_LAT_SILENT_FROM_PUBLISH`; F6b-F6f add the vk replay, a 900 ms gap, a host that sleeps
  1.5 s, and a 33 s stall. It stays 710/0 on older trees.
- `audit_faultiso.py`: **PASS**, 24 functions from 12 roots:
  - the ruling reads, the loss and recovery decisions, the lost record, the drain, the latch, the
    recovery frontend, the hook and the node release;
  - forbidden: the stage 2/3 list plus `*_relaxed`, `ioread/iowrite` and `__raw_*`;
  - `--selftest`: a planted accessor in each of six paths is caught;
  - boundaries as audit_mc2: `g5p_render_run`, `g5p_rv3_wait`, `g5p_rv3_publish_3d`, `g5p_recover`.
- Every multiclient2 gate on this tree:
  - test_render_close 938/0, loss 117/0, recover 56/0, sched 49/0, window 66152/0;
  - audit_mc2 PASS (`g5p_ru_*` fault helpers added to its allow-list);
  - tahang1 gate 10767/0 with 11 mutants caught;
  - test_kexec_quiesce 107/0, bind 177/0, admit 83/0;
  - audit_kq / audit_ram_only PASS (`g5p_ru_ruling` allowed).
- `W=1` compile of `drivers/gpu/{drm/,}apple-g5p`: no warnings. checkpatch `--strict`: 0 errors;
  the warnings are the usual `Co-Authored-By` form and MAINTAINERS for the new header.

## 6. Device plan (orchestrator; warm-safe)

Payload `build/boot/m1n1-linux-faultiso-abd31016e2.bin`, entry `faultiso-abd31016e2`
(`build/gpu/faultiso/faultiso-abd31016e2-entry/`, per-file sha256 in `build/gpu/faultiso/entry.sha256`,
payload sha256 `7c574273d4c80b4ca280b03511f574e4e36323b8010bb735e5c541dbc6179950`; `Image.gz`
`1a2b39af293e9d078789562c4b99568bbbf8979af9757ea296c2009328730eda`). Release `7.3.0-rc1-gabd31016e2bf`.

One boot, reached by warm kexec from the running desktop (any deskfold-lineage kernel, including a
wedged one). It needs no DFU and no owner step except touching the screen during F1 (labwc draws only
on damage). `SSH=scripts/omarchy/ssh.sh`, `P=/sys/module/a9_gpu_drm/parameters`. Keep
`PVR_G5P_GROUPS=last-read` (boot-desk does).

**B. Boot**, the same launcher and environment as the current desktop:
```
scripts/omarchy/desk/boot-desk.sh build/boot/m1n1-linux-faultiso-abd31016e2.bin faultiso-abd31016e2
$SSH 'uname -r; P=/sys/module/a9_gpu_drm/parameters; for p in render_user_recover render_user_test_fault_job render_user_test_stall_job render_user_drain_ms render_user_hard_ms render_user_fw_window; do printf "%s=%s " $p "$(sudo -n cat $P/$p)"; done; echo'
$SSH 'sudo -n dmesg | grep -E "RENDER-V3 (terminal|refused)|RENDER-USER attached"; sudo -n grep "LABWC-A9 FRAME" /run/desk/labwc.log | tail -1'
```
PASS:
- `7.3.0-rc1-gabd31016e2bf`, and the parameters `render_user_recover=0 render_user_test_fault_job=0
  render_user_test_stall_job=0 render_user_drain_ms=45000 render_user_hard_ms=60000`;
- labwc `RENDER-USER attached TA=8 3D=7 …`, FRAME advancing on touch, no `terminal` line.

**Fixture**, §11.3's latency101 fixture with its readback oracle:
```
BIN=build/gpu/latency101/mesa-a9-frames/bin/a9-first-frames.aarch64
$SSH 'mkdir -p /tmp/fi && chmod 0777 /tmp/fi && cat > /tmp/fi/a9-first-frames && chmod 0755 /tmp/fi/a9-first-frames' < "$BIN"
run() { $SSH "cd /tmp/fi && sudo -n timeout -s KILL 400 ./a9-first-frames --node /dev/dri/renderD128 --abi v3 --timeout-ms 20000 --frames 10 --out /tmp/fi/$1.bgra --report /tmp/fi/$1.txt >/tmp/fi/$1.out 2>&1; echo $1 rc=\$?"; }
grab() { $SSH "sudo -n dmesg | grep -E 'RENDER-V3 (test-fault|lost|drained|drain dead|recovered|recover failed|terminal|refused|bind|unbind|close|start seq=1 )|GPU-RECOVER|DS108 gen=[2-9]|G2 (recovery|page|channel|unsupported)|RENDER-USER attached|G5P VM release' | tail -${1:-30}"; }
run c0; grab 6      # control: rc=0, bind slots=10/9 ret=0, unbind why=close … ret=0
```

**F1. One client job faults on the GPU beside labwc** (the owner keeps touching and dragging from
just before `run f1` until it returns):
```
$SSH "echo 1 | sudo -n tee $P/render_user_recover; echo 3 | sudo -n tee $P/render_user_test_fault_job"
run f1
$SSH "echo 0 | sudo -n tee $P/render_user_test_fault_job"
grab 40 | tee /tmp/desk/fi-f1.txt
$SSH 'sudo -n grep "LABWC-A9 FRAME" /run/desk/labwc.log | tail -1; tail -5 /tmp/fi/f1.out'
run f2; grab 8
$SSH 'sleep 30; sudo -n grep "LABWC-A9 FRAME" /run/desk/labwc.log | tail -1'
```
PASS (record which signature, and every time):
1. `RENDER-V3 test-fault seq=S job=3 slots=10/9 va=0x3fc0000000: TA control stream unmapped`, then
   one of:
   - **(a) fault event:** `G2 recovery event …` / `G2 unsupported/fault event=4 …` (its "reboot
     required" text is the old decoder's wording), then `RENDER-V3 lost seq=S engine=0 slots=10/9
     why=ta-fault: context lost, device recovers` within a few ms of the publish;
   - **(b) silent:** `RENDER-V3 lost … why=ta-silent: context lost, device recovers` about 1-2 s
     after the publish;
   - **(c) stuck engine, firmware alive:** `RENDER-V3 lost seq=S engine=0 … drain_ms=45000: context
     lost, device kept, channel blocked` at about 60 s, then `RENDER-V3 drain dead … why=timeout`
     about 45 s later. labwc is frozen for those ~105 s; that is still a PASS if everything below
     follows.
2. `GPU-RECOVER why=lost-job ret=0 stage=done ms=<M> gen=2 end=recover`, the `DS108 gen=2` lines
   with `failed=0`, then `RENDER-V3 recovered n=1 contexts=<C> unbound=<U>`.
3. **labwc re-attaches in the new epoch** (STATUS's open item): its next job prints `RENDER-V3 start
   seq=1 … attach=firmware-dc3` and `RENDER-USER attached TA=8 3D=7 …`. Then FRAME `n` advances on
   touch, still advances 30 s later, and the screen updates.
4. f1 `rc≠0`: frames 1-2 MATCH, frame 3 fails (`EIO` for a/b, `ETIMEDOUT` for c), later frames fail
   `EIO` or are not submitted. Its close is `plan=release` or `plan=unbind` with `retained=0`, plus
   its `G5P VM release`.
5. f2 `rc=0` with all frames MATCH. It binds `10/9`, or attaches the epoch if it runs before labwc's
   next frame.
6. None of: `RENDER-V3 terminal … failed=1`, `recover failed`, `refused`.

Outcomes that are not a PASS:
- **(d) no fault:** f1's job 3 completes (a mismatch, or even a match). The page did not fault
  (it was read as zeros or a sink). Isolation was not exercised: record it; the hook is wrong, not
  the isolation.
- **FAIL:**
  - `GPU-RECOVER … ret≠0` or `RENDER-V3 recover failed` (the device is failed and labwc stops);
  - labwc not re-attaching after `recovered`;
  - a `terminal … failed=1` line.

  Collect `dmesg` and `/run/desk/labwc.log`, then **warm kexec back** to the previous desktop
  kernel with boot-desk.sh. Since deskfold, the quiesce has recovered wedged and failed devices
  (`GPU-KQ … plan=-+cycle`, STATUS rules). DFU only if that hop fails.
- **A hang** (no SSH): the owner power-cycles; pstore console from the next boot.

**F2 (only after F1 PASS): repeatable, second recovery in the same boot.**
`echo 2 > $P/render_user_recover; echo 2 > $P/render_user_test_fault_job`, then `run f3` while the
owner touches, then `echo 0 > …test_fault_job`. Same PASS with `n=2`/`gen=3`, then `run f4` rc=0.
Each recovery leaks the old epoch's firmware pages (a few hundred KiB).

**Close:**
- every parameter back to 0 (`render_user_recover`, `render_user_test_fault_job`);
- copy `/tmp/fi/*.txt *.out` and the greps to `docs/gpu/experiments/logs/faultiso/`;
- the boot stays as the desktop, or warm-kexec to the usual desktop kernel;
- if F1 and F2 pass, the owner decides whether `render_user_recover` defaults to 1.

## 7. Open items

- Event type 8 (firmware job kill) has a lighter iOS path (command 0xa, no restart); not handled.
- A stuck engine with a live firmware takes 105 s to reach the recovery. Shortening it needs
  evidence for a bound that cannot hit the natural stalls (up to 33 s seen).
- Faults while no job runs still fail the device (no job to blame).
- The vpds-side fix (the uniform store size) is in the vpds lane.
