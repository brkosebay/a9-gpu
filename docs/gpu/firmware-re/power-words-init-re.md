# Power words init: why `meta+0x6d4 = 1` stops TA dispatch, and what iOS writes

2026-11-01. Research lane only: no device access, no kernel build, no commit. Sources:
the pinned C0 LDR (`build/gpu/firmware-re/{10000000,80000000,20000000}.minim.dis`),
the j71t 16.7.10 kernelcache in the shared Ghidra (read-only decompiles; three
comments added by earlier rounds are quoted, no renames), the live j71t ADT dump
(`build/firmware/private/IODeviceTree.txt`), and the latency123 kernel branch
(`/src/wt/latency123` in the `linux-a9-src` volume, read only). Behaviour input:
[wake-latency-re](wake-latency-re.md), [README](README.md), and the latency123
boot logs (`../experiments/logs/latency123/boot{1,2,3}-*`).

Labels: **[V]** verified against instruction bytes / decompiled code here,
**[D]** derived, **[N]** from a sealed note, **[U]** undetermined.

## 0. Answers in brief

1. **The firmware never changes the GPU operating point.** The only store into
   the Fender aperture at offset `0x1000` in the whole image is the "sync"
   branch of the pstate setter (`0x1001c924`), and **all four call sites of
   `0x1001c88c` pass the mode that skips that store**. The other branch only
   waits for `(Fender+0x1000 & 0x10) == 0` and records the target frequency in
   `priv+0x2c4` (the tick divisor). Consequences: the "pstate request →
   Fender/PMGR ack that never completes" and "voltage never applied so the
   transition hangs" models are **wrong for this firmware** — nothing is
   requested. `Fender+0x1000` stays at the host's `0x11` for the whole boot (log
   evidence: `pol34=00000001` in boot 2, i.e. `fender_state & 0xf == 1`).
2. **The firmware changes the GPU *clock* through `Fender+0x1004`, and only in
   `0x1001cec8`**, whose first action is the SLC/BIF/MMU maintenance
   `0x1001b638`. `0x1001b638` is therefore not only a per-tick housekeeping
   task: it is the mandatory pre-clock-change drain. That is why `meta+0x6d4`
   and the pstate table sit on the same path as dispatch.
3. **The single most likely reason boot 2/3 wedged: `policy+4 = 16` armed the
   firmware's policy sampler, and once the policy state reached
   `policy+8 = 2` every dispatch became the governor's verdict
   (`0x1000d044` → `0x1000c0d8`). The governor's decision is gated by a window
   of `24000 * policy+4` = 384 000 ticks of the GPU's own `R0+0x160` counter; if
   that counter does not advance on Linux, the window never closes and the
   governor never grants dispatch, so the engine start word (`global+0x72c`) is
   never written.** The `meta+0x6d4 = 1` arm adds a second, tick-rate caller of
   `0x1001b638` (which the same clock-change path also runs first) and is
   predicted to wedge too — §4.3 ranks the two, §4.4 gives the bisect
   predictions, §4.5 the single read that separates them.
4. **`policy+8` is the firmware's policy state, `policy+0xc` its companion.**
   iOS *initialises* them to **0 / 4** — the word at `0x5940c18` is
   `00 00 00 00 04 00 00 00` — exactly what Linux writes. The `2 / 2` seen in
   earlier v0 RAM is a **runtime** value written by the firmware's own state
   machine (`0x1000d044` treats `+8 <= 1` as "always ready", `+8 == 2` as
   "consult `+0xc` and then the governor"). So the gate is inert at boot on both
   sides and live once the firmware reaches state 2 — which is why the governor
   can refuse dispatch on a boot where the state machine advanced.
5. **`policy+0x34` is the current pstate index** (read live from
   `Fender+0x1000 & 0xf`), not a flag and not a MHz value. The tick handler and
   the dispatch gate both read it as "GPU parked at state 0 → do nothing".
6. **GPU voltage:** the AGX kext has no voltage write and no PMGR perf-domain
   call on the GPU's behalf. On A9 the GPU frequency is written by the firmware
   (`Fender+0x1004`) and the *power state* by the host (`Fender+0x1000`); the
   voltage for that state is produced by the SoC's hardware DVFS (PMGR
   `bridge-settings-N` / `dvd-*` / `ps-regs`, `perf-regs`), i.e. it follows the
   clock with no OS-visible per-state write. The ADT's per-state voltages
   (`perf-states` second column) are **read by iOS and then discarded** — they
   only inform the host of the envelope; they are not programmed anywhere.
   §7 has the Asahi-style DT/kernel answer.
7. Boot 3's limits are **not** an iOS replay. `setPerformanceLimit` converts a
   Q16 fraction into hundredths of a pstate index and floors it at
   `acc+0x5b8` = `gpu-pwr-min-duty-cycle` = 40, so the reachable default range
   is 40 (0.40) … 400 (4.00). 400/400 pins the envelope to a single point
   (pstate 4.00) and disables the range the governor is supposed to use.

## 1. The dispatch path, from the MTS kick to the engine start [V]

Main loop (`0x8000a0f0..0x8000a3a0`), with the veneers mapped:

```
8000a120  policy veneer -> 0x10001ac8 -> 0x1000c0d8
8000a128  TXSTAT bit0?  no  -> 8000a1d8
8000a13c  tick handler 0x1001061c
8000a1a4  reload TXTIMER = ~((priv+0x2c4 * 1000) >> 8)
8000a1c8  store, and if the timed-out flag was exactly 1 -> 8000a120
8000a1d8  D1Ar1 = R0 + 0xb10 ; CALLR 0x10027280      ; read the MTS message
8000a20c  engine = (word & 0xf)
8000a22c  manager[0xfc] &= ~(1 << engine)            ; clear our pending bit
8000a240  CALLR 0x10003bb4(engine)   ; readiness
8000a24c  BNE -> 0x8000a36c -> CALLR 0x10002504(engine)   ; dispatch
8000a254  ... ack MTS (R0+0xb08 = 0, 0x04820010 = 0), clear TXSTAT
```

So a consumed kick does **not** imply a started engine: readiness gates the
dispatch, and `0x10002504` is what writes the engine record.

`0x10003bb4(engine)` [V]:

```
p = meta ; pol = *(u32 *)(p + 0x70)          ; the policy block (host slot 0x4a)
g = 0x1000d044(pol)                           ; policy gate  (§1.1)
if (g != 0) {
        if (0x1000d504(*(u32 *)(p + 0x74)) == 0) return 1;   /* companion passed */
        /* companion refused: fall through to the state test */
}
if (engine is not 0 and not 1) return 0
st = *(u32 *)(*(u32 *)(p + 0x64) + 0x72c)     ; engine record state
return (st == 1 || st == 2)
```

### 1.1 `0x1000d044(pol)` [V]

```
s = pol[0x08]
if (s <= 1) return 1                          ; boot value on iOS *and* Linux
if (s == 2) {                                 ; runtime, firmware-written
    if (pol[0x0c] == 0) return 1
    if (*(u32 *)(priv + 0x244) != 0) goto slow ; priv+0x244 = cached meta+0x6e0 (1)
    if (pol[0x0c] == 4) return 1
}
slow:
if (pol[0x34] == 0) return 0                  ; GPU at pstate 0 -> never dispatch
if (*(u32 *)(meta + 0x74) == 2) return 0
return 0x1000c0d8(pol)                        ; the governor's return IS the verdict
```

The engine record word `+0x72c` is the same word the host samples as
`idle[2]` (`g5p_rv3_sample()`: `global + 0x724 + 4*i`). `busy=-1` with
`idle=00000000,...` in boot 2/3 therefore means: **the firmware never wrote the
engine start word** — the job was consumed and dropped or deferred, not started
and stalled. That is consistent with the absence of any fault event, with the
heartbeat continuing, and with the host timing out.

So the **only** place where the firmware's own policy can veto a dispatch is the
governor call at the bottom of `0x1000d044`. Boot 2/3 never wrote the engine
word, which is exactly what a governor veto looks like, so that veto is ranked
first in §4.3. (The `+8 <= 1` short circuit is what makes boot 1 immune until
the state machine advances.)

## 2. The pstate setter: two branches, and the write is never taken [V]

`0x1001c88c(index, mode, timeout)`:

| mode | path | what it does |
|---|---|---|
| 0, 2 | `0x1001c8a8` | `0x10007a94(ap0, 0x1000, value=0, mask=0x10, tmo)` = **poll `(Fender+0x1000 & 0x10) == 0`**, no store; then `priv+0x2c4 = table[index]` |
| 1, 3 | `0x1001c8d0` | `policy+0x34 = index`; `priv+0x2c4 = table[index]`; `0x1001f130()`; `Fender+0x1000 = index\|0x10`; for modes 1/2 additionally poll `(Fender+0x1000 & 0x1f) == index` |

Proof of the "poll, no store" reading of `0x10007a94`: its prologue saves
`arg3 → D0.7` and `arg4 → D1.7` (`0x10007aa8/0x10007aac`), the loop body is
`read; and mask; cmp expected` (`0x10007b3c..0x10007b50`), and there is no
store on that path. `0x10007a44` is the plain writer (`SETD [base+off], value`).

Call sites of `0x1001c88c` and their mode:

| site | mode | index | caller role |
|---|---|---|---|
| `0x1001ca44` | **2** | from `meta+0x710` bounds | governor entry |
| `0x1001cbc0` | **2** | `[sp-12]` (the policy target) | perf update, after the delay check |
| `0x1001ccd0` | **2** | `[sp-12]` | alternate perf branch |
| `0x1001d070` | **0** | 0 | the shutdown/park path |

Cross-check: the only *store* into the Fender aperture at offset `0x1000` in the
image is `0x1001c924` (the mode-1/3 write); `0x1001c8b8` only sets up the poll of
the same offset. Because no site uses mode 1 or 3, **the firmware never writes
`Fender+0x1000`**. The GPU's GPC
performance state is 100 % host-owned, which matches the host-side contract
recorded in Ghidra on `AGXMetaFender_setGPCPerformanceState` (`0x6ca4ccc`):
wait for `+0x1000` bit 4 clear (10 µs steps, panic after ~30 s), store
`state|0x10`, keep the shadow at `fender_obj+8`, return — no wait for the
target state.

**Therefore: rule out a pstate-transition handshake as the cause of boot 2/3.**
No state was requested, no ack was awaited, and `policy+0x34` stayed 1.

## 3. Where the firmware does change the clock: `0x1001cec8` [V]

`0x1001cec8` (single caller: `0x10009840`, inside the work/policy function that
starts near `0x1000968c`):

```
0x1001cee4  CALLR 0x1001f11c
0x1001ceec  CALLR 0x1001b638          ; <-- THE MAINTENANCE (SLC/BIF/MMU)
0x1001cf00  CALLR 0x1001cce0(target)  ; Fender+0x1004 = target   (ap0, off 0x1004)
0x1001cf18  CALLR 0x10007a44(0, 0, D1.6 | 0x2000)   ; Fender+0x0 |= 0x2000 (kick)
0x1001cf28  CALLR 0x10007a6c(0, 0)    ; read Fender+0x0 back
```

`Fender+0x1004` is the frequency request; `Fender+0x0` bit 13 is its kick. This
is the firmware's DVFS write, and it is **preceded** by the maintenance.

`0x1001b638` itself (unchanged from README §"Firmware maintenance"):

```
if (0x100111fc() != 0)  -> 0x100125b4(0x11f00, 1)     ; variant-A kick
else                    -> R0+0x11f00 = 1
R0+0x688  = 7            ; then poll (0x688 & 7) == 0
R0+0x1720 = fw_global[0x44] | 0x1f ; then poll (0x1728 & 1) == 0
0x1001dfec(0xffffffff, 1) -> 0x1001b5c4: R0+0xe230 = 0xc03 ; poll (0xe238 & 1) == 0
R0+0xe138 = 0x804        ; then poll (0xe138 & 0x804) == 0    ; all-context invalidate
```

`0x100018c8` is the poll engine for all four: `read [R0+off]; and mask; cmp
expected`, with `0x1000d138()` (returns 1 when `*(u32 *)meta[0x74] == 2`) as the
abort test. In the maintenance calls the timeout argument is 0, i.e. **no
timeout** — a poll that never clears parks the firmware in that loop.

Callers of `0x1001b638`: the tick handler (`0x100106e8..10c`, gated by
`meta+0x6d4`), `0x1001cec8` at `0x1001ceec` (the clock-change path, §3), and
`0x1001f6b0` in the DM/event-scan region. So the routine is reached from three
independent places; only the tick one is gated by `6d4`, and the clock-change
one is gated by nothing at all.

### 3.1 What `0x1001c9b8` does before it can reach that point [V]

```
0x1001ca48..0x1001ca90  read R0+0x160/0x164 high/low/high (consistent 64-bit timer)
0x1001cabc..0x1001cad4  a second consistent 64-bit sample
0x1001cb08  if (priv+0x228 == 0 || priv+0x22c == 0) -> use priv+0x270 as divisor
0x1001cb38  f = priv+0x2c4                       ; current table MHz
0x1001cb68  delta = (now - t0) << 8
0x1001cb9c  q = udiv64(delta, f)                  ; 0x10022f88
0x1001cba4  if (q <= 1) retry the timer sampling   ; <- the "delay" gate
0x1001cbb0  pstate_set(target, 2, 250)            ; poll-only, §2
```

`0x1001c9b8` (called from `0x100095c8`, `0x10009eec`, `0x1000a0cc`,
`0x1000ae68`, `0x1000cad8`) and `0x1001cec8` (called from `0x10009840`) are two
entry points into the same work/policy path: the first does the delay check and
the pstate bookkeeping, the second does the maintenance and the actual
`Fender+0x1004` write.

`priv+0x228` = `meta+0x6c0` = 1 and `priv+0x22c` = `meta+0x6c8` = 1 in every
latency123 boot, so the divisor is `priv+0x2c4`. `priv+0x2c4` is written by
`0x1001c838` = `table[Fender+0x1000 & 0xf]`, and `0x1001c838` is called from the
sampler `0x1000ab10` on every policy sample. With `meta+0x71c = 340` always
written (the `META71C` boot parameter) and Fender state 1, `priv+0x2c4` is 340
**in boot 1 as well** — the "our zeroed table gives 0xFFFFFFFF / division by
zero" model in wake-latency §1.1 does not hold once `0x1001c838` has run. That
removes "divide by zero" as the boot-1/boot-2 difference and shifts the
difference to the words that only boot 2/3 have.

## 4. What `6d4` and `policy+4` actually gate, and the ranked cause

### 4.1 `meta+0x6d4` [V]

`priv+0x240 = meta+0x6d4` gates the tick handler's second-modulus block
(`0x100106e0..110`), which calls `0x10020784`, `0x10019700`, and `0x1001b638`.
Value 1 = every tick. Nothing else reads it. It does **not** appear in
`0x1000d044`, `0x10003bb4`, `0x10002504`, or `0x1001cec8`; its only route to
dispatch is the shared `0x1001b638` body. It also does not gate the
`0x1001cec8` maintenance, which runs unconditionally from the work/policy path.

### 4.2 `policy+4` [V]

Three consumers in the image:

| site | use |
|---|---|
| `0x1000aa80` (`0x1000abfc`) | `n = udiv64(24000 * policy+4, X)`, clamped up to 625 (`0x271`), stored at `policy+0x918`; the sample window |
| `0x1000ac28` | the same value used as a divisor |
| `0x1000c708` | `policy+4 * 24000` written to `policy+0x900/+0x904` as the governor deadline |

With `policy+4 = 0` the window and the deadline are both degenerate (0, clamped
to 625 ticks); with 16 they are 16 ms / 384 000 AIC ticks, which is exactly the
ADT (`gpu-power-sample-period = 16`, and README's "companion clock count
384000"). So **`policy+4` is what turns the sampler from a no-op into a real
16 ms decision loop**, and the sampler is what feeds the target that
`0x1001cbb0`/`0x1001cec8` consume.

### 4.3 The candidate wedge sites, ranked

| rank | site | mechanism | fits the evidence |
|---|---|---|---|
| 1 | **governor veto**: `0x1000d044` bottom branch → `0x1000c0d8` returns 0 | once the firmware's policy state reaches `policy+8 = 2` (the `2/2` seen in v0 RAM), every dispatch is decided by the governor. The governor's decision is gated by the sampler's window, `udiv64(24000 * policy+4, X)` clamped to 625 ticks of the **GPU's own `R0+0x160` counter** (`0x1000abfc`, `0x1000ac28`). With `policy+4 = 16` that window is 384 000 ticks; **if `R0+0x160` does not advance, the window never closes and the governor never grants dispatch.** With `policy+4 = 0` the window is degenerate and the state machine never needs it | yes: TA consumed then silence, no fault, no error word, heartbeat and MTS ack continue; and it is the only host-word route to "no engine start" (the engine-start test is *after* this call) |
| 2 | `0x1001b638` first poll, `(R0+0x688 & 7) == 0`, entered from the clock-change path `0x10009840 → 0x1001cec8` because `policy+4 = 16` armed the sampler | the clock change never happens (`Fender+0x1004` unwritten) and the work/policy bookkeeping behind it never runs; the poll has no timeout, so the firmware parks with no error | partly: explains a traffic-dependent stall, not by itself a 60 s wedge with only one kick consumed |
| 3 | same routine, entered from the tick (`6d4 = 1`) | same body, 1 kHz instead of per decision | partly, same caveat |
| 4 | `0x1001d074` self-branch (engine park) | would freeze the loop entirely | no: the heartbeat and MTS ack continue for the whole 60 s |

Ranks 1 and 2 share the same arming word (`policy+4 = 16`) and differ only in
*which* wait freezes: the governor's window in the policy block, or the
maintenance's hardware poll. Ranks 2 and 3 share the same routine and differ
only in the caller (`policy+4`-armed work policy vs `6d4`-gated tick).

The "Fender/PMGR ack never completes" and "voltage never applied" explanations
are **not** supported as *requests*: no state is requested (§2). The e138
invalidate poll comes *after* three earlier polls, so a firmware stuck in
`0x1001b638` is stuck earlier than e138.

### 4.4 Prediction for the added bisect

| boot | prediction | if it instead wedges |
|---|---|---|
| `6d4 = 1` only (table still zero, `policy+4 = 0`) | **no wedge**: the tick-side maintenance runs, but the policy state machine never needs the sampler window and the work/policy path never decides on a clock change | the tick-side maintenance alone is enough → rank 3, and the maintenance body is the whole story |
| `policy+4 = 16` only (`6d4 = 0`) | **wedge**, identical to boot 2: the sampler window becomes real and the governor refuses | `policy+4` is not the arming word → rank 3 |
| both (boot 2) | wedge (observed) | – |

If both arms wedge, the next discriminator is the *tick period*: a slower table
(e.g. `{0, 34}` = 10 ms) should scale a tick-driven stall by 10× and leave a
window-driven stall unchanged.

### 4.5 The one device read that settles it

**`R0+0x160` (`0x201000000 + 0x160`), the GPU's 64-bit `ROGUE_CR_TIMER`, read
twice ~1 ms apart during the TA wait (high/low/high, as the firmware does).**

* **It does not advance** (or reads 0 / `0xffffffff` / a value whose high and
  low words never agree) → every firmware deadline is frozen: the governor's
  `24000 * policy+4` window can never close, and the perf-update delay check
  `udiv64(delta << 8, priv+0x2c4) > 1` can never pass. **Rank 1 confirmed**; the
  fix is a host-side time source the firmware can actually read, not a policy
  word.
* **It advances normally** → the window closes, so the veto is not the clock;
  then read `R0+0x688` (below) to test rank 2.

Second-tier read, only if the timer is alive: **`R0+0x688`** — `(0x688 & 7) != 0`
means the firmware is inside `0x1001b638`'s first poll. Then `R0+0xe238`
(`& 1`) and `R0+0xe138` (`& 0x804`) name the other two parks.

Both registers live in blocks that are powered while a job is in flight, so
these reads are inside the "never touch registers after retire" rule. The driver
already maps both windows (`window[0]` = `0x201000000`, `window[1]` =
`0x201e00000`); one `readl()` per `g5p_l123_pause()` is enough.

## 5. Everything iOS's host driver writes into meta / ctl / policy

Writer map (all [V] from decompiles):

| object | writer | notes |
|---|---|---|
| meta, ctl | `AGXMetaFirmware_initializeSharedRootsAndControl` `0x6c96be8` | one-shot, before release |
| meta pstate table, policy+4, policy+0x3c.. | `0x6c974f0` (tail-called as `vtable+0xb0`) | the policy export; not a defined function in Ghidra, decoded from `build/gpu/agx.dis` |
| ctl+0x1028/102c/1030 | `AGXAccelerator_setPerformanceLimit` `0x6c9a74c` | Q16 client limits → "hundredths of a pstate" |
| policy coefficients | `AGXFirmware_importPowerCoefficients` `0x6c95770` | ADT keys → accelerator `+0x5b8..+0x6d4`, later copied into the firmware policy |
| meta+0x6d4, +0x6dc, ctl+0x00 | the power-state updater `0x6c9db40` | case 0 sets `6d4 = 1` and copies the domain mask |
| `Fender+0x1000` | `AGXMetaFender_setGPCPerformanceState` `0x6ca4ccc` | host only; poll bit4, store `state\|0x10` |

### 5.1 meta block (`0x6c96be8`, `0x6c974f0`)

| word | iOS value | meaning |
|---|---|---|
| `+0x6c0` | `(g_7b3f402 >> 1) & 1` | enable the utilization accumulator |
| `+0x6c4` | `(acc+0x510 >> 8) & 1` | |
| `+0x6c8` | `acc vtable+0x768()` | timing-table selector (1 on this platform) |
| `+0x6d0..0x6d7` | 0 | |
| `+0x6d8` | `0x0000000100000000` | power-bit pair |
| `+0x6dc` | the domain mask | |
| `+0x6d4` | **1 while powered**, 0 when off | per-tick maintenance |
| `+0x6e4` | 200 if `g_7b3f402 & 0x10`, else 1 | service modulus |
| `+0x6fc` | `(acc+0x510 >> 19) & 1` | |
| `+0x700` | same as `+0x6e4` | |
| `+0x710` | **4** | max pstate index |
| `+0x714` | **5** | pstate count |
| `+0x718..0x728` | **0, 340, 474, 550, 723** (Hz/10^6) | the frequency table |
| `+0x72c..0x754` | 0 | unused rows |
| `+0x758 / +0x75c / +0x760` | 0x14 / 0x4000 / 0x1020 | AIC timer tuple |
| `+0x764 / +0x768` | T1 entry / SP | |
| `+0x774 / +0x77c / +0x784` | image VA / 0x300000 / timestamp | |

### 5.2 ctl block

| word | iOS value | writer |
|---|---|---|
| `+0x00` | the powered domain mask (`meta+0x6d8`) | `0x6c97458`, and every power-state change |
| `+0x04` | **7 if `acc+0x510` bit 30, else 0** | `0x6c9704c` |
| `+0x08`, `+0x0c`, `+0x10` | 0 | `0x6c97050..58` |
| `+0x14` | `acc+0x1170` = 0 | `0x6c974c0` (confirmed negative) |
| `+0x1c` | 0 | `0x6c9745c` |
| `+0x24` | 0x78 | `0x6c970e8` |
| `+0x30` | `(acc+0x1af8 & 3) == 0` | `0x6c974b8` |
| `+0x1028` | max power, 3348 (`gpu-device-max-power`) | `setPerformanceLimit(0, q16)` |
| `+0x102c` | min perf index in hundredths; floor **40** (0.40), top 400 | `setPerformanceLimit(1, q16)` |
| `+0x1030` | max perf index in hundredths; top **400** (4.00) | `setPerformanceLimit(2, q16)` |
| `+0x1044 / +0x1048 / +0x104c` | kernel-provided / 1 | `0x6c9747c/9c` |

`setPerformanceLimit(1|2, q16)` converts a Q16 fraction to *hundredths of a
pstate index* by interpolating the **meta frequency table** (`meta+0x710`,
`meta+0x718+4i`) and then floors the result at `acc+0x5b8`
(`gpu-pwr-min-duty-cycle` = 40). So "400" means pstate 4.00, not 400 MHz, and a
request of 0 becomes 40 rather than 0. The block is zeroed at allocation, so
these three words are 0 until a power-management client sets a limit; 400/400 is
not a default anywhere in this code.

### 5.3 policy block (0x938 bytes, zeroed, then)

| word | iOS value | source |
|---|---|---|
| `+0x04` | **16** | `acc+0xc6c` ← `gpu-power-sample-period` (or AIC clks/24000) |
| `+0x08 / +0x0c` | **0 / 4** (`0x00000000 0x00000004` at `0x5940c18`); the firmware later writes 2 / 2 | constant `DAT_5940c18` |
| `+0x10` | `0x3f800000` (1.0f) | constant |
| `+0x28` | `0x0000000100000001` | constant |
| `+0x30` | 0 | |
| `+0x38` | 100 | constant |
| `+0x3c .. +0x19b` | 0x160-byte table from `acc+0x450` | **still unrecovered** |
| `+0x34` | written by the firmware (`0x1001c900`), = current pstate index | runtime |
| `+0x630 / +0x638 / +0x640` | 0 / (313−1)/313 / 1/313 | `gpu-pwr-filter-time-constant` |
| `+0x648 / +0x658` | integral / proportional gain | `gpu-pwr-*-gain` |
| `+0x650 / +0x654` | `(float2)acc+0x638/63c` | |
| `+0x660 / +0x664` | `−(min_duty·100)/max_power` / `(min_duty − 100·max_idx)` | |
| `+0x668` | 95 | `gpu-perf-tgt-utilization` |
| `+0x674` | 3348 | `gpu-device-max-power` |
| `+0x67c / +0x680 / +0x684` | 5 / 50 / 0 | `gpu-perf-filter-*` |
| `+0x688` | 4000 | `min_duty·100` |
| `+0x68c .. +0x6c0` | gains, clamps, `(n−1)/n`, `1/n`, limiter constant 5 | `gpu-perf-*` |
| `+0x6fc / +0x7cc` | 400 / 400 | frequency-limit pair |
| `+0x8f8` | `mach_absolute_time()` | |

### 5.4 The GPU `perf-states` table (live ADT, [V])

`build/firmware/private/IODeviceTree.txt` `/arm-io/sgx`:

```
perf-states = (0 Hz, 450 mV) (340 MHz, 675) (474 MHz, 781) (550 MHz, 815) (723 MHz, 881)
gpu-num-perf-states = 4
perf-state-count    = 16
gpu-perf-tgt-utilization = 95
gpu-power-sample-period  = 16
gpu-pwr-min-duty-cycle   = 40
gpu-device-max-power     = 3348
```

`docs/adt/j71t.txt` (the payload DT dump) disagrees: `gpu-num-perf-states = 2`,
`perf-states = 0`. **The live device values are authoritative**; the static
dump is stale. Arm 1's `{0, 340}` table is exactly the stale dump's shape and is
one row short of the real table even for the "off + first" subset.

## 6. Linux versus iOS: word table

Linux sources: `boot-layout.h` / `latency123.h` in `/src/wt/latency123`
(`g5p_make_init`, `g5p_l123_arm_a`), values as logged in boot 1/2/3.

| word | iOS | Linux boot 1 | Linux arm A (2/3) | needed for `6d4 = 1` to behave? |
|---|---|---|---|---|
| `meta+0x6d4` | 1 powered | 0 | 1 | yes — this is the flag itself |
| `meta+0x710` | 4 | 0 | 1 (arm 1) / 4 (arm 2) | yes: bounds the table the host and firmware both index |
| `meta+0x714` | 5 | 0 | 2 / 5 | yes |
| `meta+0x718..0x728` | 0,340,474,550,723 | 0,340,0,0,0 | full / capped | yes: `priv+0x2c4` = tick divisor = timer period |
| `meta+0x71c` | 340 | 340 | 340 | already written (META71C) |
| `meta+0x6c0` | 1 | 1 | 1 | yes (utilization accumulator) |
| `meta+0x6c4` | platform | 0 | 0 | likely: enables the per-tick `0x100044f0` |
| `meta+0x6c8` | 1 | 1 | 1 | yes (selects the table divisor) |
| `meta+0x6d8` | 0x100000000 | 0x100000000 | same | no (already equal) |
| `meta+0x6dc` | domain mask | 0 | 0 | diagnostic only (log ids) |
| `meta+0x6e4` | 1 or 200 | 1 | 1 | no change needed |
| `meta+0x6fc` | platform bit | 0 | 0 | [U] |
| `ctl+0x00` | powered domain mask | 0 | 0 | diagnostic only, but part of a faithful power-up |
| `ctl+0x04` | 7 or 0 | 0 | 0 (separate arm) | **no**: `0x100032d0(0)` returns for the TA |
| `ctl+0x1028` | 3348 | 0 | 3348 | only for a real envelope |
| `ctl+0x102c` | 40 … 400 (floor 40 = 0.40) | 0 | 400 | envelope; **arm 2's 400 is not the default** |
| `ctl+0x1030` | **400** | 0 | 400 | envelope; arm 2's value is right |
| `policy+4` | **16** | 0 | 16 | **yes — the arming word (§4.2)** |
| `policy+8 / +0x0c` | **0 / 4 at init** (`DAT_5940c18`), 2/2 at runtime | 0 / 4 | 0 / 4 | already equal at init; the 2/2 flip is the firmware's and is what makes the governor the dispatch verdict |
| `policy+0x10` | 1.0f | 1.0f | 1.0f | already equal |
| `policy+0x28` | (1,1) | (1,1) | same | already equal |
| `policy+0x38` | 100 | 100 | same | already equal |
| `policy+0x3c..0x19b` | 0x160-byte table | **absent (zeros)** | absent | **yes, for a working governor** — the largest remaining hole |
| `policy+0x630..0x708` | filters/gains | absent (variant 10 only) | absent | yes, same reason |
| `policy+0x6fc / +0x7cc` | 400 / 400 | 0 | 400 | envelope |
| `policy+0x8f8` | timestamp | timestamp | same | already equal |
| `policy+0x34` | fw-written | fw-written | fw-written | never host-written |

Two facts worth carrying forward:

* `policy+8` is 0 on **both** sides at init, so `0x1000d044` returns "ready"
  until the firmware's policy state machine writes 2 (the `2/2` in v0 RAM).
  From that moment every dispatch is the governor's verdict — on iOS and on
  Linux alike. That is why `policy+4` and the governor are on the dispatch path
  and why boot 1 (degenerate window) is not.
* Arm 2's `ctl+0x102c = 400` sets min = max = pstate 4, i.e. it tells the
  firmware the envelope has one point. Together with `policy+4 = 16` that makes
  the sampler ask for a clock the host never set. The floor is 40; use 40/400.

## 7. Who scales the GPU voltage on A9

Proven negatives first:

* **The firmware does not.** §2: no `Fender+0x1000` write is reachable; the
  firmware writes `Fender+0x1004` (frequency) and `Fender+0x0` (kick) only.
* **The AGX kext does not.** `AGXFirmware_importPowerCoefficients` reads the ADT
  and fills the policy; `setPerformanceLimit` converts Q16 limits through the
  *frequency* table and writes the three ctl words; nothing in either writes a
  voltage or a PMGR perf-domain register, and neither `perf-states`' second
  column nor `gpu-device-max-power` is pushed anywhere but the policy block.
* **The A9 PMGR's perf-state path is CPU-only.** `ApplePMGR_setPerfDomainPSLevel`
  (`0x5a3a0cc`) writes a 4-bit level into a per-domain PMGR record (and mirrors
  it to an `IOStateReporter` channel); `AppleS8000PMGR_setPerfState`
  (`0x5e92094`) writes PMGR block 9 `+0x20` with a 4-bit "current" and 4-bit
  "requested" level and waits for the ack twice. Both are reached from the CPU
  power path; no AGX vtable target calls them (the AGX power path only does
  provider clock/power `+568/+570` and Fender core/state requests, per
  [power-round2](power-round2.md)).

So on A9 the GPU operating point is a **two-owner** system:

| half | owner | register |
|---|---|---|
| power state (off/on/GPC) | host | `Fender+0x1000`, `state \| 0x10`, poll bit4 |
| frequency | firmware | `Fender+0x1004` = MHz, then `Fender+0x0 \|= 0x2000` |
| voltage | **hardware**, follows the frequency | PMGR `dvd-*` / `bridge-settings-N` / `perf-regs` / `ps-regs`, configured once by the PMGR driver from the ADT |
| envelope | host | `ctl+0x1028/102c/1030` |

The `perf-states` voltages (450/675/781/815/881 mV) are the *characterisation*
of that hardware DVFS curve, not a table the OS programs. They are still the
right thing to put in the DT: they let Linux check a requested state against a
voltage the hardware can produce, and they are what a future devfreq/OPP table
needs.

### 7.1 Asahi-style answer

**Device tree** (both are needed, and they are different things):

```
gpu@201000000 {
        compatible = "apple,s8003-gpu";           /* g5p */
        apple,perf-states = <0 450>, <340000000 675>, <474000000 781>,
                            <550000000 815>, <723000000 881>;
        apple,num-perf-states = <4>;              /* max index, live ADT */
        apple,perf-tgt-utilization = <95>;
        apple,power-sample-period-ms = <16>;
        apple,min-duty-cycle = <40>;
        apple,max-power-mw = <3348>;
        power-domains = <&gfx>;                   /* PMGR gate 124 */
        ...
};

pmgr: power-management@20e000000 {
        compatible = "apple,s8003-pmgr", "apple,pmgr";
        /* the SoC DVFS domains, exactly as the live ADT has them */
        apple,voltage-states1 = <0x00028676 546>, <0x0001aaaa 575>, ...;
        apple,perf-regs = <...>;                  /* 0x30/0x34 in the ADT */
        ...
};
```

The `pmgr` part is the A9 analogue of mainline's `apple,pmgr` +
`apple,voltage-statesN` binding: a small driver that (a) claims the domain's
`ps-regs`/`perf-regs` window, (b) exposes a `clk` for the GPU's frequency and a
regulator/OPP entry per `voltage-states` row. Upstream Asahi does this per-SoC in
`apple-soc`/`apple-pmgr` and derives the GPU OPPs from the same ADT keys.

**What the kernel fills in at init** (the `g5p` driver, before firmware
release, because the meta/policy copy at `0x100009ac` is one-shot):

1. the frequency table (`meta+0x710/714/718..`) from `apple,perf-states`,
2. `policy+4` from `apple,power-sample-period-ms`,
3. the governor coefficients from the ADT keys (`gpu-perf-*`, `gpu-pwr-*`) —
   once `policy+0x3c` is recovered,
4. the envelope (`ctl+0x1028/102c/1030`) from max-power / min-duty / num-states,
5. `meta+0x6d4 = 1` **only after** the envelope and the table are in place.

**What it must not do:** write `Fender+0x1000` after boot, or expect the
firmware to. Linux's `0x11` at boot is the correct and only host value. If real
DVFS is wanted, the *host* has to own it: write `Fender+0x1000` (bit4-clear
first) and `Fender+0x1004` from a devfreq policy, with the same
maintenance-before-clock-change ordering the firmware uses.

## 8. The ordered list

### 8.1 The iOS-faithful order (run with `6d4 = 1` and the full table)

Ordered because the firmware copies meta/ctl once at init (`0x100009ac →
0x10003ca4`) and because the clock-change path reads what the earlier steps
left.

1. **Zero the root/shared blocks; build the object layout.** (existing)
2. **Frequency table** — `meta+0x710 = 4`, `meta+0x714 = 5`,
   `meta+0x718..0x728 = 0,340,474,550,723`, `meta+0x72c..0x754 = 0`.
3. **`meta+0x71c = 340`** (the fallback divisor; already done by META71C).
4. **Timing/platform words** — `meta+0x6c0 = 1`, `meta+0x6c4 = 1`,
   `meta+0x6c8 = 1`, `meta+0x6d8 = 0x100000000`, `meta+0x6e4 = 1`,
   `meta+0x704/0x708/0x70c` (already written), `meta+0x758/0x75c/0x760`
   (already written).
5. **Policy init constants, exactly iOS's** — `policy+4 = 16`,
   `policy+8 = 0`, `policy+0xc = 4`, `policy+0x10 = 0x3f800000`,
   `policy+0x28 = (1,1)`, `policy+0x38 = 100`, `policy+0x8f8 = now`.
6. **The 0x160-byte policy table at `policy+0x3c`** and the filter block
   `policy+0x630..0x708` from the ADT. **Still unrecovered — this is the step
   that blocks the faithful configuration.** Without it the governor runs on
   zero coefficients.
7. **Envelope** — `ctl+0x1028 = 3348`, `ctl+0x102c = 40`, `ctl+0x1030 = 400`,
   `policy+0x6fc = 400`, `policy+0x7cc = 400`. Never 400/400.
8. **Power-state words** — `ctl+0x00 = meta+0x6d8` (the powered domain mask),
   `meta+0x6dc = the same`.
9. **`meta+0x6d4 = 1`** — last.
10. **Host-owned power state** — leave `Fender+0x1000 = 0x11`; do not poll for
    `(reg & 0x1f) == 1` after writing (iOS does not).
11. **Do not** write `ctl+0x04 = 7` in the same boot; iOS's own value is
    platform-conditional and `0x100032d0(0)` returns for the TA anyway.
12. **If a new operating point is ever wanted**, the host must perform the whole
    sequence: poll bit 4 clear, write `state | 0x10` to `Fender+0x1000`, then
    `Fender+0x1004`, then `Fender+0x0 |= 0x2000` — the same order
    `0x1001cec8` uses, maintenance included.

### 8.2 What is safe today, without step 6

Keep **`policy+4 = 0`** and **`policy+8 = 0`** (iOS's boot value). The sampler
window stays degenerate, the governor never takes the dispatch verdict, and the
firmware behaves as in boot 1: dispatch works, completion is slow. `meta+0x6d4`
may be set to 1 in that configuration *only* because the tick-side maintenance
does not gate dispatch — but see §4.4: if the `6d4 = 1`-only bisect arm wedges,
that sentence is false and `6d4` must wait for step 6 as well.

## 9. One iOS boot: exact reads for the orchestrator

Rebooting into iOS ends the Linux session, so this is a single list. All reads
are object/RAM reads; **no MMIO** (`kread()` in `tools/ios-gpu-probe` refuses
device apertures, and a userspace MMIO read panics iOS).

### 9.1 Run the existing capture (gives everything the probe already knows)

```sh
IPAD_PW=... scripts/gpu/ios-probe-run16.sh          # fw_*, root_*, pm_*, PM trace
```

`fw_before/after` = the whole `AGXMetaFirmware` object (0xcd8 bytes);
`root_before/after` = the control/root block through `fw+0x400/0x408/0x2f0`.
If the captured block is the 0x2058-byte control object only, add the 0x4244-byte
root object through the same edge (both are plain DMA-coherent RAM).

### 9.2 The words to read, with exact offsets

Root block offsets are the fixed `g5p_plan_layout()` order (same on iOS, since
it is iOS's own `c95da0` allocation order): `policy` (slot 0x4a) = **root +
0x3040**, `meta` (slot 0x4d) = **root + 0x3a00**, `ctl` (slot 0x5e) = **shared
+ 0x0000**.

| what | address | expected on iOS | identity check |
|---|---|---|---|
| policy sample period | `root + 0x3044` | 16 | **yes** — settles `policy+4` |
| policy state pair | `root + 0x3048` | 0 / 4 at boot, 2 / 2 while running | **yes** — tells us which state the runtime `2/2` belongs to |
| policy current pstate | `root + 0x3074` | 0..4 | **yes** |
| policy filters | `root + 0x3670 .. 0x3748` | nonzero gains | |
| policy table | `root + 0x307c .. 0x31db` | the 0x160 bytes | the missing blob |
| `meta+0x6d4` | `root + 0x40d4` | **1** | **yes** — settles arm A's fidelity |
| `meta+0x710/+0x714` | `root + 0x4110`, `+0x4114` | 4, 5 | **yes** |
| `meta+0x718..0x728` | `root + 0x4118 .. 0x4128` | 0,340,474,550,723 | **yes** |
| `meta+0x6c0/+0x6c4/+0x6c8` | `root + 0x40c0/c4/c8` | 1, ?, 1 | |
| `meta+0x6dc/+0x6e4` | `root + 0x40dc`, `+0x40e4` | mask, 1 or 200 | |
| `ctl+0x00` | `shared + 0x0000` | domain mask | |
| `ctl+0x04` | `shared + 0x0004` | 7 or 0 | settles the `ctl+0x04` question |
| `ctl+0x10` | `shared + 0x0010` | 0 | settles the inhibit mask |
| `ctl+0x1028/102c/1030` | `shared + 0x1028/102c/1030` | 3348, 0 or 40…400, 400 | **yes** — the envelope |
| `Fender` state shadow | `AGXMetaFender` object `+0x08` | 1 | no MMIO; the host's own copy |
| accelerator limits | `AGXAccelerator + 0x5b8`, `+0x5cc/5c8/5d4/5e0` | 40, …, 1 | the imported policy inputs |
| accelerator num states | `AGXAccelerator + 0x1c4c` | 4 | cross-check vs the ADT |
| sample period | `AGXAccelerator + 0xc6c` | 16 | cross-check vs `policy+4` |
| PMGR domain records | `ApplePMGR` object, per-domain record `+0xc` | level 0..n | the cached "current PS level", no MMIO |

Identity checks that let a failed read be *detected* rather than believed: the
policy `+4` must be 16 and the meta `+0x718` row must be `340` before the
offsets are trusted; if they are not, the block is not the one this document
describes.

### 9.3 What each read decides

| read | decision |
|---|---|
| `ctl+0x04` | 7 → arm B is a faithful replay; 0 → it is a counterfactual probe |
| `meta+0x6d4` | 1 → arm A's flag is real; 0 → the power state we run is not case 0 |
| `policy+4` | 16 confirms the arming word; also confirms the ADT chain |
| policy state pair | at boot 0/4; **2/2 while a job runs** confirms the gate is live and the governor owns the verdict |
| `meta+0x710/718..` | confirms 4/5 and the five rows (vs the stale dump's 2) |
| `ctl+0x1028/102c/1030` | confirms the envelope (3348 / min / 400) and that 400/400 is not what iOS ships |
| `AGXMetaFender+8` | the live GPU state the host last set (should be 1) |
| `policy+0x3c..` | the last missing init blob |

The GPU timer (`R0+0x160`) cannot be part of this list: userspace MMIO reads
panic iOS and `kread()` refuses the aperture. Its advance is a **Linux** read
(§4.5); the iOS boot only needs to confirm that the firmware's own clock is
alive indirectly, via `policy+0x918` (the sampler's stored window) advancing
between two captures.

## 10. Open

* `policy+0x3c..0x19b` (the 0x160-byte table from `acc+0x450`) is still the
  largest hole; without it any `meta+0x6d4 = 1` configuration runs a governor
  with zero coefficients.
* Whether `R0+0x160` advances on Linux is the pivot of §4.3. It is one read
  (§4.5) but it has not been taken; if it is dead, every firmware deadline on
  this path is suspect, not just the governor's window.
* Whether the tick-side maintenance alone (rank 3) can starve dispatch is not
  settled by bytes; the bisect in §4.4 and the `R0+0x688` read settle it.
* `0x1000d044`'s `*(u32 *)(meta + 0x74) == 2` abort condition and the
  `priv+0x244` bypass are firmware globals with no host word behind them as far
  as this round found.
* The runtime value of `meta+0x6e4` (1 vs 200) on this SoC is still unknown; it
  changes only how often the *service* block runs, not the maintenance.
* The governor body `0x1000c0d8` was only entered here (branch table at
  `0x1000c154` on `policy+8`); its three state branches were not traced to their
  return values. That trace is the next static step if the bisect says
  `policy+4` is the arming word.
