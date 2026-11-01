# Firmware timers, the "idle" loop, and what wakes it: does the tick gate TA/3D completion?

2026-11-01. Research lane, reverse engineering only: **no device access, no kernel
build, no commit.** Sources: the pinned C0 LDR (`build/gpu/extracted/agx-g5p-c0.ldr`,
SHA-256 `33ae20cb…`), the existing MINIM listings
`build/gpu/firmware-re/{10000000,80000000,20000000}.minim.dis`, the shared j71t
kernelcache in Ghidra (`scripts/ghidra.py`, read-only, no renames), and the sealed
latency notes (latency113 (`../experiments/latency113-ta-dispatch.md`, not published),
latency120 (`../experiments/latency120-late-completion-tail.md`, not published),
latency122 (`../experiments/latency122-snapshot-side-effect-audit.md`, not published)) as the
*behavioural* input. All firmware addresses below are MINIM execution PCs unless
marked `R0` (the RGX register window, `META 0x02000000`), `PRIV` (`0x8000a000`,
`*(GbP+0x6c)`), `priv+N` (the private block), `meta` (init block, root slot `0x4d`),
`ctl` (boot-control block, root slot `0x5e`), or a kernelcache VA.

Labels: **[V]** verified against the instruction bytes here, **[D]** derived,
**[N]** taken from a sealed note, **[U]** undetermined.

## 0. Answers in brief

1. **The firmware programs exactly one timer: the META core timer at
   `0x04800020`** (`TXTIMER`). It is loaded with `~((f_MHz*1000)>>8)` where `f` is a
   host-written pstate frequency, so Apple's table gives a ~1 ms tick and our
   zeroed table gives `0xFFFFFFFF`. Its expiry is `TXSTAT` bit 0, and its handler is
   `0x1001061c`. Nothing in either image programs the AIC/PIO timer
   (`0x20e101000`); the firmware only *reads* it (`0x100223f0`). No path in the
   firmware produces a 5000 or 1024 ms constant — the 5.000 s and ~1.024 s lattices
   can only be tick counts times a tick period, or products of host-written
   policy words (§1.2).
2. **There is no idle state to wake.** `HALT`, `SLEEP`, `WFI` and `RTI` occur
   **zero** times in all three firmware images. The "idle" is the spin loop
   `0x8000a120..0x8000a1d8`, which polls `TXSTAT`, services the MTS ring, and runs
   the DVFS governor on every pass. Therefore "the GPU completion does not wake the
   firmware" is the **wrong model**: the firmware is always awake and its event
   vector is open and prompt (297–590 events/s). What is gated is not the wake but
   the *state machine that publishes completion*.
3. **Two of the gating words are host-written and our bootstrap leaves them zero;
   the third is the tick period.** `ctl+0x04` is a three-bit **per-engine
   tick-service mask** (iOS: 7 when a platform flag is set, else 0; per tick
   `0x100032d0(0/1/2)`, where the TA call returns immediately and the 3D/CL calls
   run the job state machine). `meta+0x6d4` is a **flag, not a modulus**: 1 makes the
   tick's cache-maintenance block run *every* tick, 0 skips it — and it is exactly
   the word that selects the firmware's `688`/`1720`/`e230`/`e138` maintenance
   sequence (`0x1001b638`), i.e. the "flush after tiling" that
   `docs/gpu/STATUS.md` names as the one known cache-handling difference from iOS.
   iOS's power-state updater sets it to 1 when the GPU is powered (`0x6c9db68`,
   case 0 of the table at `0x594111e`) and clears it on power-down (`0x6c9dbc8`).
   `ctl+0x14`, which earlier rounds suspected, is **0 on iOS too** (new negative,
   §3.2) — it is not the difference.
4. **What this can and cannot explain.** The tick cannot explain why CPU memory
   traffic changes the latency by 20× (`STATUS.md`: ~20 s idle, ~1 s under churn);
   that dependence says the *stall* is a visibility wait. What the tick controls is
   the *flush* and the *poller*: with `meta+0x6d4 = 0` the firmware never issues its
   own maintenance sequence, and with a zero pstate table its poll cadence is
   undefined. Smallest host-side test: **`meta+0x6d4 = 1` plus the pstate table**
   (§4.2), with `ctl+0x04 = 7` as the orthogonal second arm.

## 1. The firmware's timers

### 1.1 The META core timer (`0x04800020`) — the only programmed timer [V]

| item | value |
|---|---|
| register | `0x04800020`, 32-bit, in the META core MMIO block `0x04800000..` that the host reaches through the slave port (`core.inc`, `ROGUE_CR_META_SP_MSLV*` = `0x0A00..`); `MOV TXTIMER,#0` at `0x10000460` is the only *special-register* operand |
| arming sites | `0x8000a1c8` (scheduler loop, once per tick pass), `0x10020334` and `0x10020360` (helper `0x100202f0..0x10020368`, the boot-time arming) |
| value | `~((f*1000)>>8)`, `f` = `priv+0x2c4` when `priv+0x228 && priv+0x22c` are both non-zero, else `priv+0x270` |
| `priv+0x2c4` source | `0x1001c838`: `priv[0x26c + 4*(R1+0x1000 & 0xf)]`, i.e. `meta+0x718 + 4*pstate` — the **pstate frequency table** (Apple `{0,340,474,550,723}` MHz) |
| `priv+0x270` source | `0x1000122c..123c`: `meta+0x71c` (pstate-1 frequency) |
| expiry | `TXSTAT` bit 0 (`MOV D0.5,TXSTAT; TST #1` at `0x8000a128`), cleared by `MOV TXSTAT,#1` at `0x8000a144` / `0x8000a2bc` |
| handler | `0x1001061c` (see 1.3) |
| initial state | `crt0` writes `TXTIMER,#0` = **timer stopped** at boot (`0x10000460`) |

Consequences of the arithmetic, all [D]:

* With Apple's table at pstate 1, `(340*1000)>>8 = 1328` counts; the design value is
  1 ms if and only if the timer counts at `f/256` (core clock ÷ 256 at the same
  pstate), which is the only reading under which `f*1000>>8` is a period.
* With our table (all zero) **both** branches load `0xFFFFFFFF` (the
  `priv+0x270` branch is `meta+0x71c = 0` too, and `0x10020314` shows the
  `priv+0x22c`-clear branch also uses `priv+0x270`). `~((0*1000)>>8) = 0xffffffff`,
  which is also what device reads of `0x04800020` returned on non-faulting runs
  (`ds-r33-b-warm-reset.md:187`: `0xffffffff` or `7..0xe`) [N].
* Therefore on Linux the tick is either ~never (2^32 counts) or degenerate, and on
  iOS it is a real, frequency-derived period. That is the single largest
  host-visible difference on this path.

What a device read settles (§5): two reads of `0x04800020` a known interval apart,
on a boot with the table zeroed and on one with `meta+0x71c = 340`. A downward
count between `0xfffffacf` and `0xffffffff` proves "counts down from the loaded
value"; a value near 0 or a wrap in ≪ 1 s proves the opposite polarity; a value that
does not move proves the timer is stopped or the polarity model is wrong.

### 1.2 The other time sources (read-only)

| source | use | evidence |
|---|---|---|
| `ROGUE_CR_TIMER` = `R0+0x160/0x164` (64-bit; 48-bit VALUE, bit 63 = `BIT31` rollover flag, so the high/low/high retry is what makes a long read consistent) | the firmware's own clock: `0x100221e8..0x100222ac` samples high/low/high with retries around the PIO read; every deadline comparison in the governor and the poll engine uses it | [V] `0x100223f0` / `0x100221e8`; `ROGUE_CR_TIMER 0x0160` and its 64-bit mask in `build/gpu/ds/pvr_rogue_cr_defs.h:944-951` |
| PIO tuple 1 (`0x20e101000`, "AICTimerRegs", offset `meta+0x760` = `0x1020`) | read-only cross-check of the above; `0x1002220c/0x1002222c/0x100222ac` are loads | [V] register table in `FUN_6c7fd9c` comment; README "AIC timer registry is read, not programmed" |
| 24 MHz arithmetic | `24000` ms→ticks in `0x1000abfc`, `0x1000ec00`; `policy+4 * 24000` sets the governor's `+0x900/+0x904` deadline (`0x1000c708`) | [N] README §"New verified iOS inputs" |
| MTS background-timer schedule | `R0+0xb00` values `0/1/2` = DM n, BGCTX, NON_COUNTED, BG_TIMER written by the DM queue scan `0x1001f6a4` | [N] wake111 §2.1 |

Nothing programs the PIO/AIC timer, and no firmware path loads `5000`, `10000`,
`35000`, `60000`, `0x7270e00` or `24e6*5` (latency113 §5 checked the same
constants). The 5.000 s / ~1.024 s lattices are therefore **not** literals in the
firmware.

### 1.3 What the tick handler `0x1001061c` actually does [V]

Entry conditions: `TXSTAT` bit 0 set. The loop clears the event (`TXSTAT = 1`),
re-arms the timer, and calls this. Body, in order:

| PC | action | gated by |
|---|---|---|
| `0x10010634..44` | `++[GbP+56]` — the tick counter | always |
| `0x1001064c` | if `priv+0x170 & 2` → log id `0x105` (`0x10017688`), then continue | `ctl+0x00` bit 1 |
| `0x10010658` | `0x10001328`: copy six boot-control words into `priv+0x170..0x188` | `ctl+0x00`, `+0x04`, `+0x10`, `+0x14`, `+0x18`, `+0x20` |
| `0x10010664..90` | if `priv+0x180 != 0` and `tick % priv+0x180 == 0` → `0x10002f94(2)` | **`ctl+0x14` (modulus 1)** |
| `0x100106a0..b8` | if `tick % priv+0x248 == 0` → the "service block" at `0x100107ac` (rows below) | `meta+0x6e4` (modulus 3) |
| `0x100106c0..dc` | runs after **either** branch above: if `policy+0x34 == 0`, return from the handler (skips the modulus-2 block and the state machine) | `policy+0x34` |
| `0x100106e8..10c` | second modulus (`tick % priv+0x240 == 0` → `0x10020784`, `0x10019700`, `0x1001b638` = cache maintenance + `11f00` preamble). `meta+0x6d4` is only ever 0 or 1 (a flag, §3.3), so 1 means *every* tick | **`meta+0x6d4`** |
| `0x10010714..3c` | `ctl+0x28` state machine (1 → timestamp-stability loop, 2 → increment) | `ctl+0x28` |
| `0x100107ac` | `0x1000fcbc`: timestamp-delta utilization accumulators (returns at once if `priv+0x228 == 0`) | `meta+0x6c0` |
| `0x100107b4` | `0x100161a8`: "source-2 backup dispatch" — if pending (`[x+0x14c]`) and `0x10003bb4(2)` readiness passes, clears `[x+0x14c]` and tail-calls `0x10013a18(2,0)`, which sets engine-2 scheduler-record `+0x34` and runs `0x10011254` | always called inside the modulus-3 block; two log ids gated by `ctl+0x00` bit 5 |
| `0x1001095c..9ec` | if `priv+0x234 != 0` and `0x1000d044(policy)` passes: `0x10017420(engine+0x78c, 1)`, `(+0x83c, 0.5)`, `(+0x76c, 2)` — scheduler weight/priority writes | `meta+0x6dc` |
| `0x100107dc..800` | if `priv+0x174 & 1` → `0x100032d0(0)`; `& 2` → `0x100032d0(1)`; `& 4` → `0x100032d0(2)` | **`ctl+0x04` bits 0/1/2** |
| `0x10010808..2c` | if `priv+0x230 != 0` → `0x100044f0` | **`meta+0x6c4`** |

The three `0x100032d0` calls are the interesting ones. `0x100032d0(engine)`:

```
100032e8  D0Ar6 = [priv+0x17c]          ; engine inhibit mask = ctl+0x10
100032fc  TST   (1<<engine),D0Ar6
10003300  BNE   0x10003344              ; bit set -> no service at all
10003308  CMP   engine,#1 ; BEQ 0x100034bc    ; engine 1 -> 3D/VDM service
10003314  CMP   engine,#1 ; BCC 0x10003370    ; engine 0 -> test below
10003320  ...                           ; engine >= 2: CL state test, [state+0x1832]==1
10003370  CMP   engine,#2 ; BNE 0x10003344    ; engine 0 is not 2 -> RETURN
1000339c  engine 2: if [CL-state+0x1832]==1 and [+0x1824]==0 ->
          0x10012aa8, 0x100270cc, 0x10011a14(+0x720), 0x10011a74,
          0x10011358, 0x100116f0(0x15), 0x10018728 …
100034bc  engine 1: if ([+0x1844]-1) <= 2 and [+0x1828]==0 ->
          0x10012aa8, 0x100270cc, 0x10011a14(+0x724), 0x10011a74,
          0x10011358, 0x1001bf88, 0x10011358, 0x100116f0(0x13),
          0x1001bf74, 0x10012438(+0x724), 0x10011218 …
```

`0x10011a14(state+0x724)` / `0x10012438(state+0x724)` are the same "start25 points at
state+0x724" entry the host-side notes call the 3D job state machine. So **the tick
advances the 3D and CL job state machines; it does not touch TA.** That is the only
place in the firmware where a job's stage transition is driven by a timer.

Caveat on the modulus: the entire service block, engine dispatch included, sits
*inside* the `tick % priv+0x248` branch. Linux sets `meta+0x6e4 = 1`, so the block
runs on every tick — the most eager possible setting. iOS sets 200 when
`global_7b3f402` bit 4 is set and 1 otherwise (`6c970c0..6c970d0`). If the live iOS
value were 200, tick-serviced stages would carry a ≥ 200 ms floor on iOS, so that
config bit is probably clear on a production phone; a single read of `meta+0x6e4`
on iOS decides it, and a 200 would lower arm A's prior (§4.2) accordingly.

### 1.4 What the tick does *not* do

* It does not poll `EVENT_STATUS` (`R0+0x130`), the TA/3D finish bits, or
  `R0+0xb20`. Completion collection is the ISR (`0x80009edc` → `0x100037d4`, §2.2),
  not the tick.
* It does not read or write `R0+0xb00` (the MTS kick word). The kick is a plain
  store the firmware never acknowledges.
* It does not reprogram the timer with anything but the pstate expression.

## 2. Idle and wake

### 2.1 There is no idle loop to wake [V]

`HALT`, `SLEEP`, `WFI`, `RTI`: **0 occurrences** in `10000000.minim.dis`,
`80000000.minim.dis`, `20000000.minim.dis` (only `SWAP TXMASK/TXMASKI` appears),
and `SWITCH` — the META instruction that is the only way a thread enters HALT
(TRM §2.2.39) — is **also 0**. The firmware has no sleep path, no halt instruction
and no interrupt-return instruction; the thread is a permanent poll loop. The shape
is:

```
8000a120  CALLR policy veneer (0x8000a4b4 -> 0x10001ac8 -> 0x1000c0d8)
8000a128  D0.5 = TXSTAT
8000a130  TST  D0.5,#1
8000a134  BEQ  8000a1d8            ; no timer pending -> service MTS
8000a13c  CALLR 0x1001061c         ; tick handler
8000a144  TXSTAT = 1               ; clear timer event
8000a1a4  reload 0x04800020
8000a1d0  if TXSTAT was exactly 1 -> 8000a120 (next pass, no MTS this pass)
8000a1d8  fetch/serve the MTS item at R0+0xb10
8000a240  if 0x10003bb4(engine) != 0 -> 8000a36c -> 0x10002504 (DC dispatcher)
```

This loop is also why the governor's timestamps step every ~4 ms
(latency122 §4 E3): the loop time *is* the governor period, and it is independent
of the tick. That observation is a direct confirmation of 2.1 — a firmware in a
timer-only idle could not have a 4 ms governor cadence that ignores the lattice.

**The host doorbell is not a wake.** `R0+0xb00 = 0x10/0x11` (Apple) is consumed by
the MTS fetch at `0x8000a1d8`; the loop already polls it every pass. Wake112's
null (4/4 `rang` TA phases still 11.4–34.8 s) is consistent: there was nothing to
wake.

### 2.2 The interrupt path, and why it is not the host-side difference

| item | value | writer |
|---|---|---|
| vector enable | `0xAD0 = 4` (level mode `0xAD8 = 1`) | firmware `0x1002025c` / `0x10020248`, literals |
| META-side event enable | `0x04820720 = 2` | firmware `0x10020278`, literal |
| META latch cleared by ISR | `0x04800018`, read + write-back | firmware `0x80009ef4` / `0x80009f54` |
| per-source ack | `0x04820010 = arg2` | firmware `0x80009f18` (and `0x8000a288` in the loop) |
| collected-event mask | `PRIV+0x2b8 = 0x83ec` | firmware `0x10020240`, literal |
| DM route | DM0 `0xB58=0xc0`, DM2 `0xB68=0x8000`, DM3 `0xB70=0x12c`, DM4 `0xB78=0x200` | firmware `0x10020280/0x10019d04/0x10020298/0x100202a0`, literals |
| MTS ack | `R0+0xb08 = 2` | firmware `0x80009f44` |

Every one of these is a firmware literal, identical for iOS and Linux (the *same*
image runs), and was already measured equal on device at the stall
(ds-r31-a-ta-finish-event §3/§4). **No host write can close any of them**, and none
of them is a "wake" — the ISR is entered whenever `R0+0x130` latches, which the
297–590 events/s stream and the collected MMU-fault (bit 9) event show it does.

The only host state that touches this path is `R0+0x138`/`0xac8`, and the older
audits already refuted them as blockers (MMU-fault events are collected with
`0xac8` stuck at 4).

### 2.3 Which host-written words the tick depends on

Everything the tick reads before the SP is `priv+N`, filled at boot from the host
blocks (`0x10001328` and the `priv+0x228..0x250` copy):

| private word | host word | our value | iOS value | what it gates |
|---|---|---|---|---|
| `priv+0x170` | `ctl+0x00` | 0 | power/domain bitmask | bit1 = log `0x105`; bit4 = log `0x407` (`0x10013a34`); bit5 = log `0x50c` (`0x10016218`) |
| `priv+0x174` | `ctl+0x04` | **0** | **7** if platform flag, else 0 | per-engine tick service `0x100032d0(0/1/2)` |
| `priv+0x17c` | `ctl+0x10` | 0 | 0 | engine *inhibit* mask inside `0x100032d0` (0 = service all) |
| `priv+0x180` | `ctl+0x14` | 0 | 0 | tick modulus 1 → `0x10002f94(2)` block |
| `priv+0x184` | `ctl+0x18` | 0 | never written | unused by the tick |
| `priv+0x188` | `ctl+0x20` | 0 | never written | unused by the tick |
| `priv+0x228` | `meta+0x6c0` | 1 | 1 | `0x1000fcbc` utilization |
| `priv+0x22c` | `meta+0x6c8` | 1 | 1 | timer-table branch selector |
| `priv+0x230` | `meta+0x6c4` | **0** | `(acc+0x510>>8)&1` | `0x100044f0` per tick |
| `priv+0x234` | `meta+0x6dc` | 1 | 1 (mask, either 0 or the power bits) | the `0x10017420` weight writes |
| `priv+0x240` | `meta+0x6d4` | **0** | **1** while powered (0 when off) | per-tick cache maintenance (`0x1001b638` = `688`/`1720`/`e230`/`e138`) |
| `priv+0x248` | `meta+0x6e4` | 1 | **200** or 1 | tick modulus 3 (the block that holds 1.3) |
| `priv+0x2c4` | selected pstate MHz | **0** | 340 / 474 / … | **tick period** |

Only `ctl+0x14` is zero on both sides (a confirmed negative, §3.2). `meta+0x6d4` is
**not** zero on iOS, contrary to what its neighbours in the init block suggest: the
init only zeroes it (`0x6c970a8`), the power-state updater sets it (`0x6c9db68`).

## 3. iOS versus our bootstrap: the exact differences

### 3.1 What iOS writes to the same blocks [V]

`AGXMetaFirmware_initializeSharedRootsAndControl` (`0xfffffff006c96be8`,
`build/gpu/agx.dis`, decompiled here) is the writer of every boot-control field.
Instruction addresses below are exact; `ctl` = `[x19+0x2f0]`, `meta` =
`[x19+0x268]`, `acc` = `[x19+0x1f0]`. Loads/stores use the register the listing
uses (`x8`/`x9`/`x10`); the comment names the object each store reaches.

```
6c9703c  tst   x9,#0x40000000        ; x9 built from acc+0x510/+0x514/+0x516
6c97044  csel  w9,wzr,#7,eq          ; bit30 clear -> 0, set -> 7
6c9704c  str   w9,[x10,#0x04]        ; ctl+0x04 = engine tick-service mask
6c97050  str   wzr,[x10,#0x08]
6c97054  str   wzr,[x10,#0x0c]
6c97058  str   wzr,[x10,#0x10]
6c97068  str   w9,[x10,#0x6c4]       ; (meta+0x6c4) = (acc+0x510>>8)&1
6c970a0  str   w8,[x9,#0x6fc]        ; (meta+0x6fc) = (acc+0x510>>19)&1
6c970a8  str   d0,[x9,#0x6d0]        ; (meta+0x6d0..d7) = 0
6c970b0  str   wzr,[x8,#0x28]
6c970b4  str   wzr,[x8,#0x2c]
6c970d0  str   w10,[x9,#0x6e4]       ; (meta+0x6e4) = (g_7b3f402&0x10)?200:1
6c970d8  str   w10,[x8,#0x104c]      ; ctl+0x104c = 1
6c970e0  str   w10,[x9,#0x700]
6c970e8  str   w9,[x8,#0x24]         ; mov w9,#0x78  ->  ctl+0x24 = 0x78
6c972cc  str   w10,[x8,#0x6c0]       ; (meta+0x6c0) = (g_7b3f402>>1)&1
6c972d8  str   w9,[x8,#0x6cc]
6c972f0  str   w0,[x8,#0x6c8]        ; (meta+0x6c8) = acc vtable+0x768()
6c96ffc  str   d0,[x8,#0x6d8]        ; (meta+0x6d8) = qword 0x0000000100000000
6c97458  str   w8,[x9]               ; ctl+0x00 = acc+0x6dc   (power mask)
6c9745c  str   wzr,[x9,#0x1c]        ; ctl+0x1c = 0
6c9747c  str   w0,[x8,#0x1044]
6c9749c  str   w0,[x8,#0x1048]
6c974b8  str   w9,[x10,#0x30]        ; ctl+0x30 = ((acc+0x1af8 & 3) == 0)
6c974c0  str   w9,[x10,#0x14]        ; ctl+0x14 = acc+0x1170   (= 0, see 3.2)
6c974c4  ldr   w1,[x8,#0x1174]
6c974d4  blr   vtable+0x3b8          ; setter(acc, acc+0x1174)  (arg 0)
6c974ec  br    vtable+0x0b0          ; tail call
```

Note `ctl+0x24 = 0x78`, `ctl+0x30`, `ctl+0x1044/+0x1048/+0x104c` are byte-for-byte
what our `g5p_make_init()` writes (`boot-layout.h:246-249`) — those are not
differences.

`ctl+0x00` is not a constant: `0x6c9dba8..0x6c9dc2c` builds it from a per-state byte
table at `0x594111e` on every power-state change, writing the same value to
`meta+0x6dc`, to `meta+0x6d8` (the power-bit subset) and to `ctl+0x00`. So on iOS
`ctl+0x00`/`meta+0x6dc` are a *power-state bitmask*, not zero. Our bootstrap writes
neither, so both read 0 (which mostly affects log ids — `0x10016218`, `0x10013a34`,
`0x1001064c`).

### 3.2 New negative: `ctl+0x14` is not the difference

Earlier rounds proposed the boot-control `+0x14` word (the tick modulus 1) as a
suspicious host gap, on the theory that iOS fills it. It does not:
`str d0,[x19,#0x1170]` at `0xfffffff006c7d090` with `d0 = 0` (after
`movi.16b v0,#0`) is the **only** writer of the `+0x1170/+0x1174` pair in the whole
kernelcache (`grep '#0x1170]' build/gpu/agx.dis` → two hits: this store and the
`ldr` at `0x6c974bc`), so `ctl+0x14 = 0` on iOS as well — there is no nonzero
producer to copy. The `0x10002f94(2)` block is skipped on both sides. Do not spend
a boot on it.

### 3.3 The difference table

| value | iOS | Linux (`g5p_make_init`) | evidence | reaches the tick? |
|---|---|---|---|---|
| `ctl+0x04` (engine tick-service mask) | **7** if `acc+0x510` bit 30, else 0 | **0** | `6c9703c..6c9704c` vs `boot-layout.h:246-249` (only `+0x24/+0x30/+0x1044/+0x1048/+0x104c`) | **yes, directly** |
| `ctl+0x00` (power/domain mask) | the domain mask `meta+0x6d8` | 0 | `6c9dc24..2c`, §3.4 | yes — but only enables *log* ids on both paths |
| `meta+0x6c4` (`priv+0x230`) | `(acc+0x510>>8)&1` | 0 | `6c97068` | yes — `0x100044f0` per tick |
| `meta+0x6d4` (tick cache-maintenance enable) | **1** while powered | **0** | `6c9db68` (on) / `6c9dbc8` (off), §3.4 | **yes — gates `0x1001b638` every tick** |
| `meta+0x6e4` (modulus 3) | 200 or 1 | 1 | `6c970d0` | yes — with 200 the cache-maintenance block runs 1/200 ticks instead of every tick |
| `meta+0x718..0x728` (pstate MHz) | `0,340,474,550,723` | **zeros** | `6c974f0`/`6ca8a38` chain, ADT `perf-states` | **yes — this is the tick period** |
| `meta+0x710/0x714` | 4 / 5 | 0 | same | index bounds for the table |
| `policy+4` (sample period ms) | 16 | **0** | `6ca8a38`, `6c80028` | no — governor deadlines only |
| `ctrl+0x1028/0x102c/0x1030` | 3348 / 400 / 400 | 0 | `6c974f0` | no — governor only |
| `policy+0x3c`, 0x160 bytes | copied from host+450 | absent | README round3 | no — governor only |
| `R0+0xac8` (vector latch clear) | 0 written per IRQ | never written | `ca28d8` | no (refuted, r27) |
| MTS kick `R0+0xb00` | 0x10/0x11 per submit | absent on jobs ≥ 2 | `ca0124`/`ca0284` | tested null (Wake112) |

Two candidate differences remain after this table: **`meta+0x6d4`** (the per-tick
cache-maintenance enable) and **the pstate table** (tick period) — both on the same
path and both needed for the maintenance block to run — plus the independent
**`ctl+0x04`** (per-engine tick service). Everything else is governor coefficients,
which change *decisions* but not *whether a stage can run*.

### 3.4 The power-state updater: `meta+0x6d4`, `meta+0x6d8`, `ctl+0x00` [V]

`0x6c9db40..0x6c9dc30` is the host's per-state updater. `w1` = a state index
(`cmp w1,#3; b.hi` → 0..3), looked up in the byte table at **`0x594111e`**
(contents `00 19 04 11 …` = cases 0, 0x19, 4, 0x11 for states 0..3) and dispatched
through a jump table based at `0x6c9db64` with a 4-byte stride, so the table byte is
an instruction index:

| state | case → PC | what it does |
|---|---|---|
| 0 | `0x6c9db64` | **`meta+0x6d4 = 1`**, then `meta+0x6dc = meta+0x6d8` and `ctl+0x00 = meta+0x6d8` |
| 1 | `0x6c9dbc8` | **`meta+0x6d4 = 0`**, `meta+0x6dc = 0`, `ctl+0x00 = 0` (power down) |
| 2 | `0x6c9db74` | if `meta+0x6dc != 0`: the log path; else rebuild `meta+0x6d8` via `0x6c9dba8` |
| 3 | `0x6c9dba8` | clear `meta+0x6d8`, rebuild it bit by bit from the per-domain table at `+0x10b2`, then `meta+0x6dc = mask`, `ctl+0x00 = mask` |

So a **powered** GPU on iOS has `meta+0x6d4 = 1`; that is the byte the firmware
copies to `priv+0x240` and tests every tick. The consequence is the whole reason
this matters: `tick % 1 == 0`, so `0x1001b638` (`R0+0x11f00 = 1`; `688 = 7` and
poll; `1720 = (GbP+44)|0x1f` and poll `1728.bit0`; then `e230/e238` and
`e138 = 0x804`) runs on **every** tick. Linux boots with `meta+0x6d4 = 0`, so that
sequence never runs at all. That is the "firmware's flush sequence after tiling
never runs on Linux" line of `docs/gpu/STATUS.md`, with an address and a gate.

[D] The same updater explains our zero `ctl+0x00`: on a powered iOS GPU it is the
domain mask `meta+0x6d8`. On the tick path that word only adds log ids (§1.3), so it
is a diagnostic, not a fix.

**Caveat:** the state *index* is chosen by the host driver's power transition, and
which index our bootstrap's power-up corresponds to is not established here; state 0
is the one that sets the flag, states 2/3 rebuild the mask without touching it. A
live iOS read of `meta+0x6d4` (or of `ctl+0x00`) settles which case a real power-up
takes.

## 4. Conclusion

### 4.1 What the evidence supports now

Two mechanisms live here and they are not exclusive.

**(a) The stall itself is a visibility wait, not a timer wait.** `docs/gpu/STATUS.md`
records that the same job takes ~20 s with nothing else running, ~3 s with a CPU
streaming 64 MiB, and ~1 s with processes churning caches; a timer cannot be 20×
faster because of CPU traffic. Latency122's sampler collapse is the same signature at
a smaller dose (75 KB of Normal-NC loads per tick, i.e. line evictions). So the
firmware is waiting for a word that is stale in a shared cache, which is why the
duration tracks host traffic.

**(b) The *rate* at which the firmware re-checks — and whether it ever issues its own
maintenance — is host-configured, and Linux leaves all of it at zero.** That is what
the tick work adds, and it matters in three ways:

1. **No idle, no wake.** The firmware polls; the poll loop's cadence is the
   governor's ~4 ms (latency122 §4 E3), so a stale word costs at most one loop pass
   *after* it becomes visible. Nothing "wakes" the firmware, and no interrupt is
   missing (§2.2).
2. **The firmware's own maintenance sequence never runs on Linux.** `meta+0x6d4` is
   the enable, iOS sets it on power-up, we leave it 0, and `0x1001b638` is exactly the
   `688`/`1720`/`e230`/`e138` sequence (§3.4). If a dirty or stale line in the SLC is
   what keeps the completion word invisible, then this is the one host-controlled
   action that could clear it, and it is disabled.
3. **The tick, when it runs at all, is the lattice.** The reload is
   `~((f*1000)>>8)` with `f` from the host's pstate table (§1.1); the one-word
   experiment that set `meta+0x71c = 340` moved the measured lattice from 5.000 s to
   ~1.024 s (latency114), which is what a tick-derived retry cadence does when its
   period becomes defined. The 5.000 s regime is the degenerate case.

**What this does not explain:** why 1024 (not 1 or 200) tick-periods make one lattice
step, and why admission stays 19–28 ticks at 1.024 s. Those are counts inside the
policy/scheduler state machine (`policy+4 = 0` in our boots) and remain open.

### 4.2 The smallest host-side changes to test

**Arm A (aligned with the leading SLC explanation): make the firmware's own
maintenance run every tick.** RAM only, before release (`meta` =
`g5p_object(…,0x4d)`, `policy` = object `0x4a`, as in `g5p_make_init`):

```c
/* boot-layout.h, next to the existing init writes */
g5p_put32(meta + 0x6d4, 1);   /* per-tick cache maintenance (STATUS.md "flush") */
g5p_put32(meta + 0x710, 4);   /* pstate table: required for the tick to fire  */
g5p_put32(meta + 0x714, 5);
g5p_put32(meta + 0x718, 0);   /* MHz: 0, 340, 474, 550, 723 (Apple/ADT)       */
g5p_put32(meta + 0x71c, 340);
g5p_put32(meta + 0x720, 474);
g5p_put32(meta + 0x724, 550);
g5p_put32(meta + 0x728, 723);
g5p_put32(policy + 4, 16);    /* sample period; governor deadlines            */
```

Log line: `BOOTCTL maint=1 meta6d4=%08x tick_mhz=%u sample_ms=16 before_release=1`.

Prediction (this is the point of the arm): **the latency becomes independent of CPU
memory traffic.** Idle and 64 MiB-streaming runs converge, and the 20 s cycle
disappears. If the traffic dependence *survives*, `0x1001b638` is not the flush that
matters (or our `policy+0x34` gate blocks it — check that the r17-style snapshot
shows `policy+0x34 != 0`), and the flush must be looked for in the per-job finish
path instead.

**Arm B (orthogonal): `ctl+0x04 = 7`** — the per-engine tick service, one dword:

```c
g5p_put32(ctrl + 0x04, 7);   /* engines 0/1/2 tick service; iOS 6c9704c */
```

Prediction: 3D and CL phases collapse toward the tick while TA is unchanged
(`0x100032d0(0)` returns at `0x10003344`). A null says the engine state machine is
not what publishes completion in our boots.

Run A and B on separate boots. Do not combine them, and do not add the MTS kick or
the sampler to either: Wake112 (kick) and Latency122 (visibility) already cover
those.

Prior, stated so it can be argued with: **arm A is the stronger of the two.** iOS
services an engine only once every `meta+0x6e4` ticks (200 when the config bit is
set), i.e. at a ~200 ms cadence, while its applications see millisecond-scale
completion — so the per-tick engine service cannot be what publishes a completion
an app waits on. The maintenance block, by contrast, runs every tick on iOS and is
disabled outright on Linux. Arm B is still worth its one boot because it is the only
other host word on this path, and a null from it closes the tick-service question
for good.

**Note for implementers:** with our `meta+0x6e4 = 1` the service block runs on
*every* tick and the maintenance check follows it on the same pass, so arm A needs no
change to `0x6e4`. Copying iOS's `0x6e4 = 200` as well would throttle engine service
to 200 ms; do not do both in one boot.

**Read-only pre-flight (no new mechanism, ~6 words):** `0x04800020` twice a known
interval apart (settles the timer, §5), `meta+0x6d4`, `policy+0x34`, `ctl+0x00`,
`ctl+0x04` after the bootstrap. That is ds-r31-a's E3 plus the three control words,
and it costs no MMIO.

| arm | change | the prediction | a null means |
|---|---|---|---|
| A | `meta+0x6d4 = 1` + pstate table + `policy+4 = 16` | latency stops depending on CPU memory traffic; the 20 s idle cycle disappears (the firmware's own `688`/`1720` maintenance now runs every tick) | `0x1001b638` is not the missing flush, or `policy+0x34` blocks it; look for the flush in the per-job finish path |
| B | `ctl+0x04 = 7` (table untouched) | 3D **and** CL phases collapse toward the tick, TA unchanged ⇒ the engine service does matter, contradicting the 200-tick cadence argument above | the engine state machine is not the publisher in our boots |

Arm A is the Apple-faithful power-up state (`meta+0x6d4 = 1`, §3.4) and matches the
leading explanation in `docs/gpu/STATUS.md`; Latency114's one-word result is its
prior. Arm B is the only remaining host word on the tick path that gates real work.

### 4.3 One more free discriminator, if arm A runs

If the ~1.024 s lattice is a *tick count*, two single-word variants separate the
possible counters without touching the engine path:

| variant (on top of the arm A configuration) | prediction if the lattice is a tick count of the named word | prediction if it is not |
|---|---|---|
| `meta+0x6e4 = 2` | the modulus-3 service block: the lattice doubles to ~2.048 s | a 1024-tick counter elsewhere: lattice unchanged at ~1.024 s |
| `policy+4 = 16` without `meta+0x6d4` | the governor's sample deadline: lattice becomes ~1.024 s with no maintenance enabled | no change: the governor is not the counter |

A lattice that ignores both variants is not a firmware timer count at all and the
5 s regime must come from the host-independent path (§1.4: ISR → job-record
scheduler `0x80004800`), which no host word reaches.

## 5. What one device read settles

| question | single read | decision |
|---|---|---|
| is `0x04800020` a down-counter from the loaded value, an up-counter, or a compare? | read it twice, Δt known, on a boot with the table zeroed | decreasing ⇒ down-counter (period = value/clock); near-zero then jump to `0xffffffff` ⇒ wraps ⇒ same; ≤ `0xfffffacf` always ⇒ compare model; constant ⇒ stopped |
| is the tick period frequency-locked or fixed? | the same two reads on a `meta+0x71c = 340` boot vs the zero boot | ratio ≈ 2^32/1328 ⇒ reload sets the period; unchanged ⇒ core-locked 1 ms and the reload is not the period |
| does iOS write `ctl+0x04 = 7` on this SoC? | one 4-byte read of the boot-control block `+0x04` on a live iOS boot | 7 ⇒ arm B is faithful; 0 ⇒ `acc+0x510` bit 30 is clear on A9 and arm B is a *counterfactual* probe, not an iOS replay. **Caveat:** the current iOS probe cannot resolve the shared-control window (`ios-probe/run15/2-capture-min.txt:44`, `pm_f_resolve: control_gpu=0x0 … why=`), so this read may need the PM/control resolution fixed first; arm B is worth running either way, it just changes how the result is worded |
| is the maintenance enable really 1 on iOS? | read `meta+0x6d4` (or `ctl+0x00`) on a live powered iOS boot | 1 ⇒ arm A reproduces iOS; 0 ⇒ the power state we run in is not case 0 and arm A is a probe, not a replay |
| is the per-tick service really inert at `ctl+0x10 = 0`? | read `ctl+0x10` on iOS | 0 ⇒ as modelled; nonzero ⇒ the inhibit mask matters and must be reproduced too |

Nothing here can be settled from the images alone: the timer's *polarity and clock*
are hardware properties, and `acc+0x510`'s bit 30/bit 8 are runtime platform flags.

## 6. Reproduction

```sh
# the spin loop, the tick call, the reload
python3 scripts/gpu/firmware-re/inspect.py 8000a0f0 8000a3a0

# the tick handler and its gates
python3 scripts/gpu/firmware-re/inspect.py 1001061c 10010940
python3 scripts/gpu/firmware-re/inspect.py 10016320 10016400
python3 scripts/gpu/firmware-re/inspect.py 100032d0 10003480
python3 scripts/gpu/firmware-re/inspect.py 100034bc 10003600

# the boot-control -> private copy, and the timer arm helper
python3 scripts/gpu/firmware-re/inspect.py 10001328 100013b4
python3 scripts/gpu/firmware-re/inspect.py 100202e0 100203b0

# the two writers of the timer, and the crt0 stop
python3 scripts/gpu/firmware-re/inspect.py 10000440 100004e8

# iOS side (read-only)
python3 scripts/ghidra.py decompile_function_by_address address=fffffff006c96be8
grep -n '#0x1170\]' build/gpu/agx.dis
```

Decoder limits: `0x10000450`, `0x10000468`, `0x10000480`, `0x100033b8`, `0x100033c0`,
`0x10004504` are UNKNOWN in the current MINIM decoder; none of the conclusions above
depends on them, but the crt0 block that installs the vector is only partly decoded
(§2.2 relies on the notes' r27/r30 device evidence for the vector being open).

## 7. Ledger

**OBSERVED (here):** every address, byte and field in §1–§3 read from
`10000000/80000000/20000000.minim.dis` and from `build/gpu/agx.dis`; the
`0x1170` writer count; `DAT_fffffff005940c10 = 0x100000000`; the zero
`HALT/SLEEP/RTI/SWITCH` counts; the iOS control-block write list of `6c96be8`.

**OBSERVED (sealed, cited):** the 5.000 s / ~1.024 s lattices and the 35 s stage cap
(latency113/120), the 0–7 ms ring consumption and the 2 Hz-sampler coupling
(latency122), the `meta+0x71c = 340` lattice change (Latency114 F hops), the event
path enables and their device values (ds-r31-a).

**DERIVED:** the tick-period arithmetic and its two readings; "the lattice is a tick
count"; the `ctl+0x04` mechanism and its TA/3D asymmetry; the `meta+0x6d4` gate on
the firmware's own maintenance sequence; the arm A/B decision table.

**RECONCILIATION with `docs/gpu/STATUS.md`:** its leading explanation (SLC staleness,
traffic-dependent latency) and this note are compatible and complementary. STATUS
supplies the *stall* (visibility), this note supplies the *reason it is never
cleared by the firmware* (`meta+0x6d4 = 0` disables `0x1001b638`), the *poll cadence*
(the pstate-derived tick, hence the lattice), and the proof that no wake-up is
missing (§2.1–2.2).

**UNKNOWN:** the timer's polarity, clock and whether `0xFFFFFFFF` means "never" or
"immediately"; the value of `acc+0x510` bit 30 on this SoC; whether the 5.000 s
regime has a second, non-timer source; whether `0x1001b638` is the flush STATUS.md
means, and at which cache level; which of `0x100044f0` / `0x100161a8` / `0x10013a18`
publishes the type-1 completion in our boots; the UNKNOWN-encoded crt0 vector
install; which power-state index (case 0..3) our bootstrap corresponds to.
