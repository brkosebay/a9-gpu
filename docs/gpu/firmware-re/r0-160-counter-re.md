# `R0+0x160`: the GPU timer, what makes it count, and what Linux gets wrong

2026-11-01. Research lane only: no device access, no build, no commit, no Ghidra writes.
Sources: the pinned C0 LDR disassembly `build/gpu/firmware-re/10000000.minim.dis`,
`build/gpu/ds/pvr_rogue_cr_defs.h`, the live iOS power capture
`build/gpu/ios-capture-bundle/20261101T154856Z/` (+ `parsed/power-words.{md,json}`),
the latency123 logs, `patches/gpu/0006` (the PIO aperture table), and the r17 boot log.

Grades: **[V]** decoded instruction / retained value / quoted log line, **[I]** inferred,
**[U]** undetermined.

## 0. Bottom line

1. **`R0+0x160` is `ROGUE_CR_TIMER`** [V] (`#define ROGUE_CR_TIMER 0x0160U`,
   `pvr_rogue_cr_defs.h:945`), a 64-bit counter read as `0x164` (high, `& 0xffff`) and
   `0x160` (low) with a re-read until the high word is stable.
2. **It is not frozen on Linux.** r17 (an earlier Linux boot, GPU idle at a policy
   timeout) measured it live: `sample=0 lo=008ff550`, `sample=1 lo=008ff59d` 57 µs later
   [V]. It advanced 77 ticks: **1.35 MHz over that
   span, and ≤77 MHz even if the two reads were 1 µs apart** [V arithmetic] — at least
   4× below 340 MHz and ~250× below it on the measured span. So the counter *counts*, but
   only in the windows where the GPU core clock is actually on.
3. **`power-words-init-re.md` §4.3 rank 1's mechanism is wrong and is withdrawn here.**
   The governor's 16 ms window is *not* `R0+0x160`: the firmware's time base is the
   **AIC timer (PIO unit 1, `0x20e100000`)**, read by `0x100223f0 → 0x100221e8`
   (`[priv+0x268]+0` and `+8`) [V decode], multiplied by **24000** (24 MHz) [V] and
   stored as the window/deadline (`policy+0x92c = 0x5dc00 = 384 000` = 16 ms) [V capture].
   Boots 2/3's **61 events/s = one per 16.4 ms** [V log], i.e. exactly `policy+4 = 16`'s
   period [I] — so the sampler kept sampling; the window was not stuck.
4. **What `R0+0x160` really feeds is the utilization accumulator**
   (`policy+0x908/+0x90c`, `+0x910/+0x914`, `+0x918/+0x91c`) [V decode]. A slow timer
   means "the GPU is ~0 % busy", not "the deadline is frozen". That still matters: it is
   the governor's only load input.
5. **The one host-visible word that can veto dispatch forever is `policy+0x34`.**
   `0x1000d044` falls to its slow path as soon as `policy+0x8 >= 2` escapes the `+0xc`
   short-circuit, and that path **returns 0 if `policy+0x34 == 0`** [V decode].
   Linux's live value is **0** in every `RENDER-L123` line (`pol34=00000000`) [V log];
   iOS's is 1–4 [V capture]. `policy+0x34` is only ever refreshed by the sampler
   (`0x1000ab10 → 0x1001c838`, `Fender+0x1000 & 0xf`) [V decode]; the other writer,
   `0x1001c900`, sits on the unreachable mode-1/3 branch, so with `policy+4 = 0` the word
   stays at its zeroed init value forever.
6. **The live capture also removes `meta+0x6d4 = 1` from the Apple-way set:**
   `meta+0x6d4 = 0` in all six snapshots and in all three 1 ms series, including the
   19.4 s of load at pstate 4 [V]. `policy+0x8` never leaves 0 in idle/load [V].

## 1. Q1 — what `R0+0x160` is

| property | value | grade |
|---|---|---|
| name | `ROGUE_CR_TIMER`, `0x0160U`, spans `0x160` (lo) / `0x164` (hi) | [V] DDK |
| format | 48-bit value + bit 63 (`MASKFULL 0x8000FFFFFFFFFFFF`, `BIT31_SHIFT 63`) | [V] DDK |
| firmware read idiom | `hi=[+0x164]; lo=[+0x160]; hi'=[+0x164]` loop until `hi==hi'`, keep `hi & 0xffff` (`0x1001ca48..0x1001ca90`, `0x1000acb0..0x1000acf8`, `0x10022408..0x10022450`) | [V] |
| write path | none: the firmware only ever reads it; no `SETD` to `0x160`/`0x164` exists | [V negative] |
| enable bit | none in the DDK map. `ROGUE_CR_CLK_CTRL` (`0x0`) gates PM/VDM/PDS/UVS/SLC/TLA/USC/TPU/MCU_L0 only; no timer field | [V] |

Two different conversions consume it, and they disagree about its rate — which is the
tell that it is a *gated* clock, not a stable time base:

* **pstate-transition delay check**, `0x1001cb7c..0x1001cb9c`:
  `q = (delta48 << 8) / priv+0x2c4`, where `priv+0x2c4 = table[Fender+0x1000 & 0xf]`
  = the **current pstate frequency in MHz** [V]. Dividing a cycle count by MHz only makes
  sense if the counter runs on the core clock.
* **utilization accumulate**, `0x1000acb0..0x1000ad94`: `delta = now - prev` from the
  same register, scaled, shifted `>> 0x13`, accumulated into `policy+0x908/+0x90c` and
  `+0x910/+0x914` [V]. This is the governor's busy measure.
* **window/deadline** uses the *other* clock: `policy+0x918/+0x91c` = AIC time
  (`0x100223f0`), deadline = `now + 24000*policy+4` (`MULD ..,#0x5dc0` at `0x1000ae98`,
  `MOV D0Ar4,#0x5dc0` at `0x1000abfc`) [V]. With `policy+4 = 16` that is `0x5dc00`.

**What makes it count.** Not a counter-enable, not a firmware write, not a pstate
*request* — the GPU core clock itself. The complete set of host writes that can leave the
CR block clocked:

| write | value | who | grade |
|---|---|---|---|
| `Fender+0x10000` | `0xffff0000` (end of init FIFO) | host, once at startup | [V] kext `0x6c93a9c`, replayed by Linux (exp02) |
| `Fender+0x0` | `0x40000000` (bit 30) | host, once at startup | [V] same |
| `Fender+0x1000` | `state \| 0x10`, low nibble = pstate | host; iOS on every DVFS step (`AGXMetaFender_setGPCPerformanceState 0x6ca4ccc`), Linux once as `0x11` | [V] |
| `Fender+0x1004` | target MHz, then `+0x0 \|= 0x2000` | firmware `0x1001cec8` only (reached from the work/policy path `0x10009840`); the *state* write `+0x1000` is unreachable for it — all four `0x1001c88c` sites use mode 0/2 | [V] |

So: the counter is gated by the operating point, and **the host already sets the only
word that matters** (`0x11` = pstate 1). It cannot be "frozen by a missing enable" in the
sense the bisect assumed; it can only be *slow*, because the core clock stops between
jobs. Measured rate [V]: 77 ticks / 57 µs between two printks ⇒ ≥1.35 MHz, and two
separate `printk`s cannot be closer than ~1 µs ⇒ **≤ ~77 MHz**; either way, far from
340 MHz.

## 2. Q2 — what the iOS capture shows over time

The 1 ms series is change-compressed, 157 columns of meta/policy/control, no
`+0x8f0/+0x8f8`, `+0x900..+0x92c` (those exist only in the six snapshots).

**Yes, the series shows the governor's own fields, and they move under load while being
static at idle.** Time-weighted dwell, load = 19.36 s, idle = 3.86 s [V, computed from
`device/power/{idle,load,after}-series.csv`]; every number in the table is a retained
column value [V], the `Fender&0xf` reading of `+0x34` is [I] (correlation with the
sampler's store).

| field (policy+) | idle | load | after |
|---|---|---|---|
| `+0x34` pstate mirror (`Fender&0xf`) | **0**, 96.6 % | **4**, 98.8 % | 0, 96.6 % |
| `+0x0c` substate | 4, 98.4 % | **0**, 98.6 % | 4, 98.3 % |
| `+0x08` state | 0, 100 % | 0, 100 % | 0, 95.4 % / **1**, 4.6 % |
| `+0x678` utilization | 0, 76.5 %; ≤0x14 | 0x69–0x85 (105–133) | 0, 98.3 % |
| `+0x6e0` | 0, 76.7 % | **0x64 = 100**, 86.2 % | 0, 98.4 % |
| `+0x6d8` (f32) | — | 0x42c7e238 ≈ 99.9, 1137 changes | 9 changes |
| `+0x6e4` (f32) | — | 0x409e2380 ≈ 4.94, 1137 changes | — |
| `+0x6f0` request ×100 | 0x64 = 100, 100 % | **0x1f4 = 500**, 99.8 % | 100, 93.8 % |
| `+0x670`, `+0x67c`, `+0x690` (PID/filter) | 19–75 changes | 1190–1206 changes | 99–105 changes |
| `meta+0x6d4` | 0 (no transitions) | **0** (no transitions) | 0 |

Window words, from the six snapshots only [V]:

| word | value | meaning |
|---|---|---|
| `policy+0x8f0/+0x8f4`, `+0x8f8/+0x8fc` | e.g. `0xfc9fe011`, then `0xfca5bc11` | previous/current AIC time (~24 MHz); matches `t_ns` of the capture to <0.1 % |
| `policy+0x900/+0x904` | `0x9e3b96b`, `1` | governor deadline, `now + 24000*policy+4` |
| `policy+0x908/+0x90c`, `+0x910/+0x914` | `0x9e3c766`/`1`, `0x9d56140`/`1` | GPU-timer busy accumulators; **they move between snapshots**, so `R0+0x160` is alive on iOS |
| `policy+0x92c` | `0x5dc00` … `0x5f949` | measured window = 384 000–390 000 ticks = 16.0–16.3 ms |
| `policy+0x918/+0x91c` | `0x9d6160f`/`1` | sampler's stored window start |

The control block does not move: `control+0x1028 = 0x0f0b` (3851), `+0x102c = +0x1030 =
0x190` (400) in all three series [V] — note 3851, not the 3348 the earlier doc predicted,
and **400/400 is what iOS actually runs**, contra power-words §5.2/§8.1 step 7.

IOReport over 34.6 s: OFF 27.34 %, P1 0.97 %, P2 0.08 %, P3 0.06 %, **P4 71.55 %** [V].
The `policy+0x34` dwell (0 at idle, 4 under load) is the same fact from the firmware's
side: iOS powers the GPU *down* between frames. Linux never does (`Fender = 0x11`
forever, 6028 samples in latency123 §6) [V].

## 3. Q3 — Linux versus iOS, word by word

Linux boot 1 [V, `L123 boot …` lines]: `arm=1/0/0/0` (bisect arm 1 = apple6d4),
`meta6d4=0 table=0/0/0,340,0,0,0 policy4=0 ctl04=0 limits=0/0/0`. Boots 2/3:
`meta6d4=1 table=1/2/…` / `table=4/5/0,340,474,550,723 limits=3348/400/400 policy4=16`.
Every cell in the three value columns below is [V] from a quoted log line, the capture,
or a decoded store/load (the `policy+0x34` cell for boots 2/3 is [U]); the `effect` column
is [I].

| word | iOS (live) | Linux boot 1 | Linux 2/3 | effect on the timer / the governor |
|---|---|---|---|---|
| `Fender+0x1000` | 0 at idle (96.6 %), 4 under load [I, from `policy+0x34`] | **1** always [V] | 1 [V] | the only thing that gates the timer; Linux is *better* than iOS here, not worse |
| `meta+0x710/714` | 4 / 5 | 0 / 0 | 1/2, 4/5 | bounds the table both sides index |
| `meta+0x718..0x728` | 0,340,474,550,723 | 0,340,0,0,0 | full | sets `priv+0x2c4` (the delay-check divisor) only |
| `meta+0x6d4` | **0 in every sample** | 0 | 1 | nothing observed; boot 1 shows it is neutral |
| `policy+0x4` | 16 | 0 | 16 | arms the sampler; makes `policy+0x34` refresh at 16 ms |
| `policy+0x8 / +0xc` | 0 / 0 (load), 0 / 4 (idle) | 0 / 4 | 0 / 4 | `s<=1` short-circuit keeps the gate open |
| `policy+0x34` | **1..4** | **0** (`pol34=00000000`) | [U] (not logged) | **the veto**: `s>=2 && +34==0 → return 0` |
| `policy+0x3c..` | 10 nonzero f32: `3.5521, 93.8674, 12.5757, 0.10135, 0.15132` at `+0x3c..0x4c`, `41.656` at `+0xdc`, `1.012, 1.355, 1.475, 1.724` at `+0x160..0x16c` (352 bytes copied, all 6 snapshots identical) | all 0 | all 0 | governor inputs; **now recovered** |
| `policy+0x630..0x708` | `+0x638=0x3f7f2e9f`, `+0x640=0x3b516154`, `+0x648=0x3ca59586`, `+0x658=0x40a90fdb`, `+0x688=400`, `+0x694=95`, `+0x6fc=400`, `+0x7cc=400` | all 0 (`variant=7`) | 0 | PID/filters; zero on Linux |
| `ctl+0x1028/102c/1030` | 3851 / 400 / 400 | 0/0/0 | 3348/400/400 | envelope; boot 3 is 503 mW low but structurally right |

Ranking the three explanations offered:

1. **A missing clock enable — ruled out** [V]. Linux writes the same three Fender words
   Apple does (exp02), keeps pstate 1 permanently, and the counter provably advances.
2. **The pstate table / Fender state — not the timer's problem** [V/I]. The table only
   changes `priv+0x2c4` (a divisor) and the firmware never writes `Fender+0x1000` at all
   (all four `0x1001c88c` sites use mode 0/2). iOS spends 27 s of 34.6 s *below* Linux's
   pstate and its timer still accumulates.
3. **Something else — yes: `policy+0x34` plus the coefficient block** [V, mechanism;
   I, that it is what wedged boots 2/3]. `0x1000d044`:
   `s = pol[8]; if (s<=1) return 1; if (s==2 && (pol[0xc]==0 || priv[0x244]==0 && pol[0xc]==4)) return 1;
   if (pol[0x34]==0) return 0; if (0x1000d138()) return 0; return 0x1000c0d8(pol);`
   (`0x1000d044..0x1000d0d0`, `0x1000d138`). Every input after the short-circuit is a
   word Linux has wrong or zero.

Consequence for DVFS, independent of the wedge: a ~1.3 MHz timer under a governor that
divides by `priv+0x2c4` MHz reports ≈0.4 % utilization, so even a perfect word set would
never request pstate 4 on this kernel. That is the same root cause that keeps
`Fender+0x1000` at 1.

## 4. Q4 — the tests

**T0 (zero risk, settles Q1 and the wedge).** Do not write anything. Extend the existing,
already-flying `RENDER-L123` line with six RAM words read at the same moment it reads
`pol34`: `policy+0x8`, `+0xc`, `+0x34`, `+0x8f8`, `+0x91c`, `+0x92c`, plus **one** timer
read in the firmware's own idiom: `hi=readl(G+0x164); lo=readl(G+0x160); hi2=readl(G+0x164)`
retried until `hi==hi2` (`G` = `window[0]` = `0x201000000`), taken **only while a phase is
in flight** (same rule as `g5p_rv3_fw_touch`, never after retire), two samples 1 ms apart.
* Pass/fail: `pol8` staying ≤1 and `pol34 != 0` in a `policy4=16` boot ⇒ the veto is not
  the gate and the search moves to `0x1000c0d8`; `pol8 >= 2 && pol34 == 0` ⇒ confirmed.
  Timer delta ≈ 340 000/ms in flight ⇒ the timer is fine and Q1 is closed; ≈ 1 000–10 000/ms
  ⇒ the timer is the slow, clock-gated kind.

**T1 (the one-factor write that makes `R0+0x160` count).**
**Set `Fender+0x1000` (window[1] + 0x1000) to `0x14` at stage S = "after the Fender wake
trio, before firmware release"** (poll bit 4 clear, store `4 | 0x10`, dummy read — the
kext's own protocol, no wait for the target state). Everything else as boot 1
(`policy4=0`, `6d4=0`, table `0/0/0,340,0,0,0`). Then run T0's timer read.
* Pass: ≥ 3·10⁸ ticks/s at 723 MHz instead of ~10³–10⁶ ⇒ the counter follows the core
  clock, and the utilization input can be trusted.
* Risk: this is the first boot that leaves the GPU at a state nobody has run; it is a
  pre-release RAM/MMIO write, so a warm kexec is not a recovery path — fly it when a DFU
  is acceptable.

**T2 (the cheaper one-factor, RAM only, most likely to unwedge).**
**Set `policy+0x34 = 1` (root slot `0x4a`, offset `0x34`) at the same stage**, i.e.
`boot_fw_words=4a:34=1`, combined with `fw_policy4=16` and nothing else from the Apple
set. It is a bootstrap only: the sampler overwrites it with `Fender+0x1000 & 0xf` on its
first 16 ms pass, so it cannot mask a real difference.
* Pass: the first TA job dispatches (`RENDER-L123 … busy=8`, not `busy=-1`) and the
  `pol34` field reads 1.

**T3 (next combination step toward the full Apple-way set).** One boot with
`fw_meta6d4=0` (see §0.6 — iOS's live value is 0), `fw_pstate_table=2`
(`4/5/0,340,474,550,723`), `fw_limits` as `3851/400/400` (iOS's real numbers), `fw_policy4=16`,
`boot_fw_words=4a:34=1`, plus the two blocks the capture has now recovered:
`policy+0x3c..+0x4c`, `+0xdc`, `+0x160..+0x16c` (the ten f32 above) and
`policy+0x638/640/648/658/688/694/6fc/7cc`.

**What a pass looks like (all of Q4):** with the interim touch OFF (`touch:0`),
key→frame **median ≤ 0.1 s** and no `RENDER-V3 end … ret=-110` inside 60 s;
`RENDER-L123` shows a real `busy=` word, `pol34 != 0`, and (T1) the timer rate at the
pstate's MHz. With the touch ON it must stay at the current 0.07 s. If T3 wedges exactly
like boot 3 while T0 shows `pol8 <= 1`, the remaining suspect is `0x1000c0d8`'s own
arithmetic on the recovered coefficients, not the timer and not the gate.

## 5. Open

* Whether `0x1000c0d8` writes `policy+0x8 = 2` on its own, and after how many 16 ms
  samples. The capture only ever sees 0 and 1; the v0 RAM sighting of 2/2 is unexplained.
* Why iOS's `meta+0x6d4` is 0 while powered. If it is genuinely always 0, the tick-side
  `0x1001b638` maintenance is not the Apple-way coherence mechanism and the interim touch
  has no Apple counterpart to emulate.
* The `acc+0x450` table reads as eight 8-byte VA-like values
  (`0xfffffff3b7f9f900` …), not as the f32 block that lands in `policy+0x3c`; the copy at
  `0x6c974f0` is therefore conditional (`[U]`), and the recovered `policy+0x3c` bytes are
  the better source.
