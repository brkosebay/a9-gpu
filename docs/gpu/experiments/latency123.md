# Latency123: desktop frame latency — interim fix, 3D fence on the firmware event, Apple-way bisect

2026-11-01. Kernel branch `a9/gpu-latency123` on desktop124j `c02a081b6f`, worktree `/src/wt/latency123`;
config identical to combined118/124j; cmdline is 124j's (only `linux_a9.entry=` differs). Device results
are in `docs/gpu/experiments/logs/latency123/`.

## 0. Status

- **Production image: `latency123-405d9b96b9`.** It carries the interim fix (an in-flight Normal-NC read
  of the firmware pages), the 3D fence on the firmware's completion event, and the split Apple-way boot
  words. Not flown yet.
- Boot 1 (knobs image), key→frame median / max: `off` 7.85 s / 27.2 s, `evict-fw` **0.27 s / 0.28 s**.
  Of those 0.27 s, the 3D completion event arrives at 24 ms and 200 ms is the idle-stuck settle that P0
  removes.
- Boots 2 and 3 (arm A: `meta+0x6d4=1` + `policy4=16` + capped or full table): the first TA job was
  consumed but never dispatched, so 60 s timeout and DEVICE_LOST (§4). The Apple way continues as a
  bisect on the production image (§5.2).

| image | payload sha256 | what | fly? |
|---|---|---|---|
| `latency123-405d9b96b9` | `9294ed08d92c75f2ba2841609c9ecfb6f1c66b15dcb7eaa05281e38d3b811e93` | **production**: knobs + P0 + interim touch + bisect knobs + `boot_fw_words` + split `fw_*` | **yes, the new desktop base** |
| `latency123-ac4214a15f` | `c67dcf76ca4682e06f086392a1578fd0bd2ef6fc6e8376e758579f112e1108f7` | P0 + interim touch only, no knobs (branch `a9/gpu-latency123-prod`); proves the knob commits drop cleanly | optional |
| `latency123-eda5fd930f` | `1d76401c452996be5bc3a90209d40c1e759098fc5af9658171dff531083ad18a` | boot-1 knobs image (what the desktop runs now with `evict-fw`) | superseded |

Payloads are at `build/boot/m1n1-linux-<entry>.bin`, entries at `build/gpu/latency123/kernel/<entry>-entry/`,
built with `scripts/gpu/latency123-build.sh package <tip>`. The production entry's Image.gz sha256 is
`b8a6ea5c341120e3af69ad7d62788680bd1fe24dd88361c1f0134e346121e968`. Branch `a9/gpu-latency123-arma`
(`04508064f4`) holds the always-on arm A attempt. It wedges TA dispatch: **do not fly it.**

## 1. Commits on `a9/gpu-latency123` (oldest first)

| commit | kind | content |
|---|---|---|
| `eda5fd930f` | temp knobs | `boot_fw_power_words`, `fw_tick_mask`, `slc_evict_*`, `cpu_sync`, `pte_variant`, `lat123_log` (`RENDER-L123` per-job line), `pstate_probe` |
| `9d11464d7c` | temp knobs | `evict_fw_pages/_line/_lines/_op/_when`, `boot_fw_region_attrs` |
| `5bdda08efb` | **production (P0)** | 3D phase completes at the first coherent sample after the matching completion event; the settle defaults to 0 |
| `8c4823f7a8` | **production, interim (Pb)** | `g5p_rv3_fw_touch()`: one Normal-NC load per 64-byte line of root + shared before every RAM sample of an in-flight phase |
| `09eeaffaf5` | temp knob | `fw_touch_pages`: page mask of that read (default all), for a live bisect |
| `d9eba589d1` | temp knob | `boot_fw_words="SLOT:OFF=VAL,..."` (hex): extra boot-image words before release, so the power-words RE can fly without a rebuild |
| `405d9b96b9` | temp knob | arm A split: `fw_meta6d4`, `fw_policy4`, `fw_pstate_table` (0/1 capped/2 full), `fw_limits`; `boot_fw_power_words` stays as a preset |

checkpatch: 0 errors on every commit; the warnings are only the required `Co-Authored-By` trailer form
(plus two long lines in the temporary `boot_fw_words` commit). Everything runs RAM-only, except the opt-in
`pstate_probe`. No commit adds GPU MMIO.

For the multi-client merge: `g5p_rv3_fw_touch(struct g5p_boot *b)` is a standalone helper, and any RAM wait
loop (the DC3 bind loop too) can call it before it samples.

## 2. Why (the interim fix)

The firmware's root and shared pages are mapped for the GPU with attributes `0x14` (META-protect + CC,
**no SLC bypass**, unlike the page-table aliases at `0x1c`), so firmware and GPU writes to them can stay in
a GPU-side cache. While a job is in flight, a host Normal-NC read of those lines releases the TA and the 3D
at once:
- `evict-fw`: TA done 9 ms, 3D event 24 ms;
- without it, they wait for the firmware tick: TA done 1.9 s, 3D event 6.6 s median, with a 20 s sawtooth.

Neither CPU cache maintenance of the polled pages (`sync` arm, DC CIVAC every sample: 3.2 s) nor a host write
does this, and bulk CPU traffic only helps by chance (`mem`, 2.26 s). The best-supported reading is that the
host read is a coherent fabric read. It forces the GPU-side owner of a dirty line to give it up, so a reader
that takes a non-allocating path (the firmware's uncached root alias, or a bypassing unit) stops seeing the
old value. This is a hypothesis: which page and line, and whether `boot_fw_region_attrs=0x1c` would remove
the need, are still open (§5.1).

On iOS, the firmware writes its own lines back: it runs `0x1001b638` (688 / 1720 / the e230 SLC3 flush /
e138) on every tick, enabled by `meta+0x6d4`. The touch is therefore **interim**. It stays the fallback
until the Apple-way words fly (§5.2).

## 3. The 0.27 s floor (P0)

`render-user-retire.h` rule (b) used to need the 3D target to stay coherent for 200 ms after the event,
whenever the engine idle words stayed set (idle-stuck). In `evict-fw` that was 3D event 24 ms, done 226 ms.
The phase now completes at the first sample where:
- the target is coherent (ring, both stamps == seq, W words, watermarks), and
- the matching type-1 completion event has been decoded since this job's 3D publish.

That is the firmware's completion report, which is also what Asahi signals its fences on.
`render_user_idle_settle_ms` stays as an optional minimum (default 0). Expected job: TA ~9 ms + 3D ~24 ms;
boot 1 already had a 34 ms key→frame minimum on idle-clear jobs. The waiter still polls every 1–2 ms.
Event-driven wakeup and drm_sched are the next step, and are not done here.

## 4. Boots 2 and 3: what arm A wedged

Both boots behaved the same: capped table {0,340} in boot 2, full table + limits in boot 3, both with
`meta+0x6d4=1` and `policy4=16`. The first TA job was consumed at 27–51 ms, its busy word never rose, no
stamp moved, and it timed out at 60 s with -110, leaving the context terminal.

The firmware was alive throughout. Its IRQ rate was 61/s, one event per 16 ms, which is `policy4=16`'s
governor period (boot 1, at `policy4=0`: 487/s); so the rate drop is the sample period, not a hang.

Table shape and limits are ruled out (boot 3). That leaves two suspects:
- `meta+0x6d4=1`: every tick then runs `0x1001b638`: `11f00=1`, `688=7` + poll, `1720=(GbP+44)|0x1f` + poll
  on `1728`, the e230/e238 SLC3 commands, `e138=0x804`. These are unbounded polls (`0x100018c8`: yield, no
  timeout), and they may depend on state iOS sets up that we do not.
- `policy4=16`: governor-deadline-driven state that TA dispatch readiness may depend on.

The power-words RE lane (`docs/gpu/firmware-re/power-words-init-re.md`) owns the "why".

## 5. Device plan (orchestrator)

### 5.1 Make the production image the desktop

Warm kexec from the running desktop, then the same Labwc launch as desk12/124j:
```sh
scripts/gpu/latency123-boot-desk.sh build/boot/m1n1-linux-latency123-405d9b96b9.bin latency123-405d9b96b9 lat123_log=1
#   expect "L123 boot attrs=0x14 arm=0/0/0/0 meta6d4=0 table=0/0/0,340,0,0,0 policy4=0 ctl04=0 limits=0/0/0"
scripts/omarchy/ssh.sh 'sudo -n sh -s -- off touch:0 off' \
  < scripts/gpu/latency123-ab.sh > build/gpu/latency123/run/prod-ab.txt 2>&1
```
- `off` is the production default (touch on). Expect key→frame at roughly 0.03–0.08 s, the 3D phase done
  within a few ms of `td ev` in `RENDER-L123`, and `RENDER-V3 retire … decided_ms ≈ event_ms`.
- `touch:0` turns the interim read off live. It should bring back the multi-second stall, which confirms
  that the touch is what carries the fix on this image. The stall has a 60 s hard bound (boot 1's
  worst was 27 s): if a `RENDER-V3 end seq=N ret=-110` line appears, the context is terminal, so kexec
  back as above. Prefer `KEYS=6` for this phase.
- CPU cost of the interim read: about 6656 NC loads (0.5–1 ms) per 1–2 ms sample, only while a job is in
  flight. It is not counted in `RENDER-L123`.
- desk-read stays off (the script stops it). Analysis as in 124j: for each `KEY` at uptime t, the first
  `RENDER-USER job=` after t, minus t.

Optional live bisect of the touched page, same boot (`KEYS=6` is enough: released ≈ 50 ms, not released
≈ seconds). Pages are root 0–1 and shared 2–25; the map is in §7.
```sh
scripts/omarchy/ssh.sh 'sudo -n env KEYS=6 sh -s -- touch:0x3 touch:0x3fffffc touch:0x1 touch:0x2 touch:0x4 \
  touch:0x1ffff8 touch:0x200000 touch:0x400000 touch:0x800000 touch:0x1000000 touch:0x2000000 off' \
  < scripts/gpu/latency123-ab.sh > build/gpu/latency123/run/prod-touch-bisect.txt 2>&1
```
A single page that releases alone sets the narrowed default (one line in `g5p_rv3_fw_touch`). One more
boot, `boot_fw_region_attrs=0x1c` with `off touch:0`, tests whether mapping the firmware regions with SLC
bypass makes the touch unnecessary. That would be the static, Asahi-style fix.

### 5.2 Apple-way bisect boots (fly between other tests; priority order)

Each boot is one warm kexec of the production image with boot params; then run `off touch:0 off`. The
`touch:0` phase is the test: with the touch off, does the Apple word alone keep latency low? A boot that
wedges like boots 2/3 shows the first `RENDER-V3 end seq=1 ret=-110`. Kexec back to the production desktop
(§5.1) after it.
```sh
B=build/boot/m1n1-linux-latency123-405d9b96b9.bin; E=latency123-405d9b96b9
# 1. meta+0x6d4 alone (policy4 0, table as boot 1 = just the 340 word)
scripts/gpu/latency123-boot-desk.sh $B $E lat123_log=1 fw_meta6d4=1
# 2. policy4=16 alone
scripts/gpu/latency123-boot-desk.sh $B $E lat123_log=1 fw_policy4=16
# 3. meta+0x6d4 with the full table, policy4 0
scripts/gpu/latency123-boot-desk.sh $B $E lat123_log=1 fw_meta6d4=1 fw_pstate_table=2
```
Check the `L123 boot … arm=M/P/T/L` line each time. Words recovered by the RE lane fly with
`boot_fw_words=4d:6d4=1,4a:4=10,...` (hex `SLOT:OFF=VAL`; each one is logged as `L123 word`, and a bad entry
fails the map before release).

## 6. GPU pstate / DVFS

- Linux runs the GPU at pstate 1 = 340 MHz. Fender `+0x1000` reads `1` 6028 times across all logs and never
  above 1. Nothing on Linux requests another state: `apple_a9_dvfs_ro` is CPU-only, and PMGR only switches
  the gfx domain on and off.
- The firmware is the DVFS actor: `0x1001c88c` writes Fender `+0x1000 = index | 0x10` itself, from the table
  in `meta+0x710..`. That is the Asahi model: the firmware does DVFS, and the kernel provides the tables from
  the DT.
- The owner wants the full 474/550/723 MHz table. The path is: the Apple-way words fly (§5.2), the table
  then comes from an `operating-points-v2` node plus `apple,perf-base-pstate` / `apple,power-sample-period`
  (built and reverted with arm A: commit `a8ab7686af` on `a9/gpu-latency123-arma`), and GPU voltage
  handling for the states above 340 MHz is confirmed.
- With the stall fixed, 340 MHz is the throughput ceiling.

## 7. Page map (root 0–1, shared 2–25; `fw_touch_pages` / `evict_fw_pages` bits)

| page | content |
|---|---|
| 0 | root: init 0x44, channels 0x47–0x49 (with the global idle words), policy 0x4a, 0x4b, 0x4c, meta 0x4d (start) |
| 1 | root: meta tail, 0x4e |
| 2 | shared: control 0x5e, event ring 0x70, 0x71–0x73, 0x74 (start) |
| 3–20 | 0x74, 0x75, the 216 KiB object 0x4f, 0x76, 0x77, 0x6c, 0x6d |
| 21 | ring controls 0x60/0x64/0x68, ring items 0x61/0x65/0x69, 0x5c, 0x85 (start) |
| 22 | 0x85, 0x84, 0x86 |
| 23 / 24 / 25 | TA page / 3D page / PM page |
