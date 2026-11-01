# Governor gate / TA-dispatch wait with `policy+4 = 16`: static RE

2026-11-01. Read-only static lane: no device contact, no build, no commit, no Ghidra writes.
Sources: `build/gpu/firmware-re/{10000000,80000000}.minim.dis` (C0 LDR), the shared Ghidra
kernelcache (read-only decompile of `0x6ca263c`), `build/gpu/ios-capture-bundle/20261101T154856Z/parsed/power-words.json`,
the latency123 logs (`t2-dmesg`, `t3-dmesg`, `boot{1,2,3}-dmesg`, `apple6d4-dmesg`), and
`drivers/gpu/drm/apple-g5p/drm-latency123.inc` in `/src/wt/latency123`.
Prior work read, not redone: `power-words-init-re.md`, `r0-160-counter-re.md`, `dsx-nx15-fw-policy-paths.md`,
`dsx-agx-policy-readiness-annotated.md`, `ds-fw-ta-kick.md`, `latency123.md`, STATUS "Slow frames".

Grades: **[V]** decoded instruction (address given) or retained value / quoted log line; **[I]** inferred; **[U]** undetermined.
Firmware addresses are META execution addresses; "disasm" = `10000000.minim.dis` unless stated.

## 0. Bottom line

1. **The hold is not the governor gate, and static evidence cannot yet name the instruction that holds it.** Eliminated:
   - `fw=0/3ffffff/0+256/0/1` carries no firmware state (§1).
   - T2 falsifies the `policy+0x34 == 0` veto: it ended with `pol34=00000001` and wedged anyway [V].
   - `0x1000d044`'s governor branch cannot grant dispatch: it returns the `0x1000d138` result (0) whatever the governor does [V `0x1000d0ac..0x1000d0c4`].
     So "the governor's return is the verdict" (power-words §1.1, §4.3 rank 1) is wrong. That branch is reachable only if `policy+8 >= 2`, and the firmware only ever stores 0 or 1 there [N: readiness-annotated §0.2, not re-verified].
2. **Corrected fact: the firmware setter `0x1001c88c` writes `policy+0x34` and `Fender+0x1000` in every mode** [V `0x1001c8a0..0x1001c92c`, §3]. Power-words §0.1/§2 ("the firmware never writes `Fender+0x1000`; rule out a pstate handshake") is wrong. Your own `0x1001c88c → Fender+0x1000 = index|0x10` is the true reading. The setter then (modes 1, 2) polls `(Fender+0x1000 & 0x1f) == index` and **never aborts** [V `0x10007b3c..0x10007c4c`]. Nothing in the kext answers or completes that on the firmware's behalf (Q2).
3. **The job is consumed, so the hold is after readiness.** The dispatch routine `0x10002504` advances a consumer cursor (`0x10002664`), and the host sees consumption 27–33 ms after publish in every `policy+4 = 16` boot (boot 2: 32, boot 3: 27, T2: 33, T3: 33), against 0–3 ms in `policy+4 = 0` boots (boot 1, apple6d4) [V logs]. That is about two 16 ms periods [I]. After that the TA never starts (`busy=-1`, all stamps 0) while ALIVE events keep coming at 31/s [V], so the main thread is not parked in a pstate poll [I]. The remaining suspects are in dispatch or TA-start (§4).
4. **T1 (`Fender+0x1000 = 0x14` before release) is a red herring for the wedge** (§6). It is a timer-rate / DVFS experiment with DFU risk.
5. **No `SLOT:OFF=VAL` word is justified by the bytes as "the" unwedge, so I will not name one.** Do these, in order (§6):
   - **(a)** `fw_policy4=16 pstate_probe=2`: no build, one in-flight MMIO read of `Fender+0x1000` (the `ps=` field).
   - **(b)** A `policy+4` sweep (4 / 64 / 1000): no build.
   - **(c)** One small read-only logging build for the words in §6.

## 1. `fw=0/3ffffff/0+256/0/1` [V]

Format `fw=%u/%x/%u+%u/%u/%u` (`drm-latency123.inc:316`), arguments `g5p_l123.fw, evict_fw_pages, evict_fw_line, evict_fw_lines, evict_fw_op, evict_fw_when`:

| field | value | meaning |
|---|---|---|
| `g5p_l123.fw` | 0 | count of `g5p_l123_fw()` evict-knob passes; stays 0 because `us=0` (the periodic evict is off) |
| `evict_fw_pages` | `0x3ffffff` | page mask, all 26 root+shared pages |
| `evict_fw_line` + `evict_fw_lines` | 0 + 256 | line range per page |
| `evict_fw_op` | 0 | read op |
| `evict_fw_when` | 1 | `when & 1` = periodic |

These are host knob defaults, with no firmware or hardware state. It does not describe the interim production touch `g5p_rv3_fw_touch()`, which has its own `fw_touch_pages`. Consequently the wedge is not a cache-visibility artefact that the touch failed to fix: `touch` defaults on, `fw=` is unrelated to it, and `ops=0x1` is the knob echo [I; the touch itself is not logged].

## 2. Facts that change the picture

| # | claim | evidence | grade |
|---|---|---|---|
| 2.1 | Policy sampler = `0x1000aa80(policy)`. At `0x1000ab10..0x1000ab18` it does `CALLR 0x1001c838; SETD [policy+0x34], D0Re0`, and `0x1001c838` returns `Fender+0x1000 & 0xf` (`0x1001c850..0x1001c85c`). So `policy+0x34` is the firmware's mirror of the Fender pstate. | disasm | V |
| 2.2 | `policy+0x34` also gets the setter's `index` (`0x1001c900`) and the post-transition target (`0x1000ae70`). Three writers in total. `+0x34 == 0` can only mean Fender nibble 0 or a target/index of 0. | disasm | V |
| 2.3 | `pol34` is firmware-written and noisy. Completed jobs carry both 0 and 1: boot 1 seq 1–3 = 0 (jobs complete); apple6d4 (`policy4=0`, `6d4=1`) seq 1/2/3 = 0/1/0 and all complete. Wedged jobs: boots 2/3 = 1, T2 = 1, T3 = 0. So `pol34` is not a gate. In T3 the host wrote 1 and the firmware replaced it with 0 by job end. | logs | V |
| 2.4 | G1 = `priv+0x228`/`+0x22c` = `meta+0x6c0`/`+0x6c8` (copy table, nx15 §4). The sampler `0x1000aa8c..0x1000aad8`, the governor and the power tick all return if either is 0. Linux has both at 1 (and iOS too: `meta.meta_sw_flag`/`policy_enable` = 1). | disasm, capture | V |
| 2.5 | `0x1000d044`: `policy+8 <= 1` returns 1 (`0x1000d054/58`); `+8 == 2` returns 1 if `+0xc == 0`, or `priv+0x244 == 0 ∧ +0xc == 4` (`0x1000d0e0..0x1000d118`); otherwise it goes to the generic block and returns **0** after calling the governor (§0.1). | disasm | V |
| 2.6 | TA readiness `0x10003bb4(engine 0)` = `d044(policy) != 0 ∧ d504(*(root+0x74)) != 0`. `0x10003bc4..0x10003bd8`: `d044 != 0` goes to `0x10003c64`, then `CALLR 0x1000d504` and `BEQ 0x10003be0`, else `MOV D0Re0,#1` at `0x10003c88`. The engine-state fallback after `0x10003be0` is skipped for engine 0 (`SUB #1; CMP #1; BHI`). The two UNKNOWN encodings at `0x10003bec`/`0x1000d52c` are not decoded here, so the BHI-path return value is assumed 0. | disasm | V (flow) / U (UNKNOWN) |
| 2.7 | `*(root+0x74)` is the 0x28-byte companion (host slot 0x4b; `root+0x3980`). `d504`: word0 == 1 → thread-wake bookkeeping (`0x1000d580`, ends in `0x1001ee50`); word0 == 0 → `return (0x10003770() == 0)`. Word0 == 2 is the fault/recovery state (README §1814). Nothing in it depends on `policy+4`. | disasm, README | V / N |
| 2.8 | The dispatch routine `0x10002504` drains its cursors (`0x10002664`: `[cursor] = ([cursor]+1) & 0xff` until it reaches the target). That the cursor is the TA ring consumer the host observes is plausible but not proven. | disasm | V decode / I identity |
| 2.9 | Dispatch calls `0x10009034` (`0x10002744`, `0x10002abc`). It reads `policy+0x908..0x914/0x928` and the `R0+0x160/0x164` timer (hi/lo/hi loop). It is a utilization accumulator, with no wait other than the stable-high-word re-read. | disasm | V |
| 2.10 | The pstate transition machinery (`0x1001c9b8` ← `0x100095c8`, `0x10009eec`, `0x1000a0cc`, `0x1000ae68`, `0x1000cad8`) is reached only from the sampler / governor / request-handler family, i.e. from the loop-head policy call `0x8000a120 → 0x10001ac8 → 0x1000c0d8`. Dispatch does not call it. | callers grep | V |
| 2.11 | Host side: `AGXMetaFirmware_handleFirmwareControllerEvent` (`0x6ca263c`) handles only event type 0 (first ALIVE) and type 6 (completion ring). No other firmware→host event is serviced, and nothing answers a power/DVFS request. | Ghidra decompile | V |

## 3. The pstate setter in full: it stores in every mode [V]

`0x1001c88c(index=D1Ar1, mode=D0Ar2, timeout=D1Ar3)`:

```
1c890  ADDS D1.5,D0Ar2,#0        ; D1.5 = mode
1c8a0  BNE  1c964                ; mode != 0
1c964  CMP D1.5,#2 ; BNE 1c8d0  ; mode 1/3  -> store path
1c970  B 1c8a8                   ; mode 2   -> poll first
1c8a8  (mode 0 falls here)  ... CALLR 10007a94(unit0, 0x1000, expect 0, mask 0x10, tmo)   ; wait Fender+0x1000 bit4 == 0
1c8d0  (the poll call RETURNS HERE, it does not branch away)
1c8f0  GETD D0FrT,[D0FrT+#112]   ; root->policy (root+0x70)
1c900  SETD [policy+#52],D0.5    ; policy+0x34 = index
1c908  SETD [priv+#708],table[index]   ; priv+0x2c4
1c910  CALLR 1f130 (fence)
1c918  OR D1Ar3,D0.5,#0x10 ; 1c92c CALLR 10007a44 ; Fender+0x1000 = index|0x10   (plain SETD)
1c934  SUB D0FrT,D1.5,#1 ; CMP #1 ; BLS 1c974   ; mode 1 or 2
1c974  ... B 10007a94(unit0, 0x1000, expect index, mask 0x1f, tmo)   ; wait (Fender+0x1000 & 0x1f) == index
```

All four callers use modes 0/2 (power-words §2 table, not re-checked), so every call stores and, for mode 2, polls for the new state. The poll primitive `0x10007a94` [V `0x10007b3c..0x10007c4c`]:
- It re-reads `[unit+off] & mask == expect` in a loop.
- On timeout, once, it sets `D0.5 = 1` and calls `0x100219b4` with `D1Ar1 = 3` (a type-3 event).
- It then jumps back to `0x10007b3c`. The timeout is never fatal: with `D0.5 != 0`, `0x10007b58` loops until the register matches.

So a request the Fender never acknowledges parks the calling thread forever and emits one type-3 event. The kext ignores type-3 events (2.11). Timeout units and the argument layout (`D0Ar6` as the second timeout) are [U].

`Fender+0x1000` low nibble is the state, and bit 4 is the busy/ack bit that hardware clears. Linux keeps it at `0x11 → 1` [V, latency123 §6]. On iOS the same register is also driven by the host (`AGXMetaFender_setGPCPerformanceState`, `0x6ca4ccc`), so there are two writers.

## 4. The trace: job receipt → TA start, and where it can hold

```
host kick (R0+0xb00=0x10 / MTS msg, engine = word & 0xf = 0)
  8000a1d8  R0+0xb10 -> word ; engine = word&0xf ; manager[0xfc] &= ~(1<<engine)         [V]
  8000a240  0x10003bb4(0)   readiness = d044(policy)!=0 && d504(companion@root+0x74)!=0   [V]
  8000a24c  BNE 8000a36c -> 8000a444 -> 0x10002504(engine)   dispatch                      [V]
              0x10002664 cursor drain  (host sees "consumed" here, cons=27..51 ms)         [V/I]
              0x10009034 utilization accumulate (R0+0x160/164)                             [V]
              ... DM scheduler 0x80004800 -> start23 0x8000672c: 63 MMIO writes,
                  4 self-clear handshakes 0x290/0x198/0x258/0xcb8, R0+0x400=1 (ds-fw-ta-kick) [N]
  not ready -> 8000a254: ack, re-arm; the kick is NOT retried (host rang once, bells=1)     [V code / I observed]
loop head (every iteration)  8000a120 -> 0x10001ac8 -> 0x1000c0d8 governor (G1, then +8 split)
   -> sampler/governor -> 0x1001c9b8 -> setter -> Fender+0x1000 write + unbounded poll      [V]
```

Which of these can hold the TA, given the evidence:

| id | site | condition / value read | evidence for | evidence against | grade |
|---|---|---|---|---|---|
| H1 | readiness `0x10003bb4` → 0 | `policy+8`, `+0xc`, `priv+0x244`, `policy+0x34`, `companion[0]`, `0x10003770()` (RAM only) | would drop the kick silently with no retry | Consumption (2.8) shows dispatch ran; `+8 ≤ 1` makes d044 = 1 | I: unlikely, but `+8`/`+0xc`/companion are **not logged** |
| H2 | setter poll `0x10007b3c..` for `Fender+0x1000` ack | MMIO `Fender+0x1000` bit4 / `& 0x1f == idx` | `policy+4 = 16` is the thing that makes the period handler and `0x1001c9b8` run; the firmware has no abort; T3 `pol34` changed → the setter or sampler really ran | ALIVE continues, so the main thread is not parked (unless the poller is a different thread); T2's `pol34 = 1` equals the host-set Fender 1, so no transition is proven | I |
| H3 | TA-start handshakes or a GPU-clock-dependent wait inside `start23` | `R0+0x290/0x198/0x258/0xcb8` self-clear | `busy` never rises, no stamp, no fault event; consumed first | no register of this family is logged under `policy4=16` | I |
| H4 | a window/deadline dependence (`policy+0x900/0x904`, `+0x918`, `+0x92c`) | AIC timer (unit 1) vs `24000 × policy+4` | consume latency ≈ 2 × 16 ms; iOS keeps these words moving (capture) | `0x10009034` and the other readers (list in §2.9) only accumulate, none waits | U |

The honest finding is that static decoding rules out the governor gate (H1's governor branch) and the `+0x34` veto, and leaves H2/H3/H4 undiscriminated. Two cheap observations separate them (§6).

## 5. What iOS has that Linux lacks (verified diffs only)

From `power-words.json` (iOS idle/load) against the T3 set:

| word | iOS | T3 | note |
|---|---|---|---|
| `policy+0x28/+0x2c` | 1/1 idle → **4/4 under load** | 1/1 (host constant) | firmware-written target pair; T3 never lets it move (Fender never leaves 1) |
| `policy+0x34` | 0 idle → 4 load | 1 (host) → 0 (fw) | §2.3 |
| `policy+0x38` | 100 idle → 400 load | 100 | same |
| `policy+0x674` | 3851 (device max power) | not set | T3 set `ctl+0x1028 = 3851` but not this copy [V capture / log] |
| `policy+0x67c` | moves between snapshots (`3312384394` idle, `3311997515` load) | not set | filter state; [I] |
| `policy+0x630/0x668/0x680/0x684` | 0 | 0 | already equal |
| `meta+0x6c4`, `meta+0x700` | 1 | 0 | `staged=0` in the catalogue; whether any firmware site reads them was not checked [U] |
| `ctl+0x20` | 1 | 0 | `ctl.platform_510_bit0`; [U] |
| uncatalogued `meta+0x14..0x354` words | nonzero, constant (e.g. `+0x20 = 0xffc00000`, `+0x24 = 6`, `+0x2c/+0x30 = 0x2000`, `+0x340.. = 0x10220838…`) | not set | likely firmware-written boot data; [U] |
| ctl `+0x1050/+0x1054` | nonzero under load only | – | [U] |
| `Fender+0x1000` | 0 at idle, 4 under load (iOS writes it itself) | 1 forever | iOS's pstate moves; Linux's never does |

Answering the candidates in the question:
- **Fender/PMGR acknowledgement of a pstate change:** the firmware does issue the write and poll (§3), but Linux was never observed to be asked for a state other than 1. Whether the sampler's period handler asks is [U]. The only decisive read is `Fender+0x1000` in flight (§6a).
- **IRQ / event the host must deliver:** no. The kext services only event types 0 and 6 (2.11), and the host controller path doesn't answer any request from the firmware. The AIC timer is read by the firmware itself (unit 1, `0x100223f0`) and the firmware runs on its own timer window.
- **A host-side handler that answers a firmware power/DVFS request:** none exists in the kext. A DVFS write is hardware-completed (Fender), so a missing acknowledgement would have to come from the SoC side (PMGR/voltage), not from a software reply.
- **An init-path word outside policy/control/meta:** not found. The PIO table (patch 0006) is the same as before (unit 0 = Fender `0x201e00000`, unit 1 = AIC timer `0x20e100000`). The firmware's `unit 6 +0x2c` write (`0x1001b908`/`0x1001b8c0`, locked path of `0x1001c9b8`) has no table entry on Linux that I can identify [U]. The locked path is taken when `sched_table[+0x28] == 0` [N, nx15 §2.3]. This is the single most suspicious unexplained dependency on a `policy+4`-driven path, since writing through an unmapped unit would be silent.

## 6. Minimal tests

### T1 (`Fender+0x1000 = 0x14` before release) is not the answer [I, with evidence]
- The sampler already mirrors `Fender & 0xf` into `policy+0x34` (2.1). T2 shows the mirror matches (`1`) and it still wedges, so "the firmware lacks a nonzero state" is not the missing input.
- `pol34 = 0` (state 0) occurs in jobs that complete (boot 1, apple6d4). iOS sits at state 0 when idle (capture: 96.6 % of idle samples) and is at 4 under load, so a nonzero state at job time is not shown to be a precondition either way [I].
- `R0+0x160` is already alive at P1 (r17), so T1's stated purpose (make the timer count) is moot.
- T1 is a pre-release MMIO write (DFU-class risk, warm kexec is not a recovery path) and it changes the firmware's *own* pstate target, which can only make the §3 poll more likely to bite.

### (a) Observation boot, no build
`lat123_log=1 fw_policy4=16 pstate_probe=2` (+ whatever `boot_fw_words` you want). `pstate_probe` bit 1 already reads `window[1] + 0x1000` once, in flight, after the first idle sample (`g5p_l123_pause`), and prints it as `ps=%08x@ms`. Decision table:
- `ps` has bit 4 set (`0x1x`), or low nibble ≠ `pol34`: an unacknowledged request → **H2**; the missing piece is SoC-side.
- `ps = 0x1` / `0x0` and bit 4 clear: Fender is idle → H2 is out, go to H3/H4.
- `ps = 0x0`: GPU parked → fits T3 but is still not a gate by itself (§2.3).

### (b) Period sweep, no build
`fw_policy4=4`, `64`, `1000` (three boots, or one at 64 first). Read `cons=` in `RENDER-L123`.
- If `cons ≈ 2 × policy+4` the main loop and the dispatch are paced by the sampler window (H4 plausibility). If `cons` stays ≈ 33 ms for all, it is not.
- Also see whether any value dispatches. A dispatch with a large window would mean the hold is window-close-dependent.

### (c) One read-only logging build (only if (a)/(b) leave it open)
Add to the `RENDER-L123` line, read at the same moment as `pol34` and once mid-wait (≥1 s, in flight):
- `policy+0x8`, `+0xc`, `+0x28`, `+0x2c`, `+0x30`, `+0x34`, `+0x38`, `+0x900..+0x92c`;
- `companion[0..9]` (`root+0x3980`, 0x28 bytes);
- `meta+0x6c0/0x6c4/0x6c8`;
- in-flight reads of `R0+0x290`, `+0x198`, `+0x258`, `+0xcb8`, `+0x430` (the existing in-flight MMIO read rule applies).

This resolves H1 (RAM words), H3 (handshake bits), and H4 (the window words).

### Not recommended
- A guessed `boot_fw_words` write. The only candidate with static support is H2, and its remedy would be a SoC-side pstate acknowledgement, which is not a word to set.
- Setting `meta+0x6c8 = 0` to disable the machinery: an earlier control (r73c case02, `dsx-agx-policy-readiness-annotated.md` §0 item 4) published exactly one ALIVE with `6c8 = 0` for a reason nobody has explained, so the result would not be clean.

## 7. Corrections to earlier docs

| doc | claim | status |
|---|---|---|
| `power-words-init-re.md` §0.1, §2, §3 | firmware never writes `Fender+0x1000`; "poll, no store" | **Wrong** (§3: the poll falls through into the store path, `0x1001c8c8 → 0x1001c8d0`) |
| same §0.3, §1.1, §4.3 rank 1 | governor return is the dispatch verdict | **Wrong**: `0x1000d0c4 MOV D0Re0,D1.5` returns the `d138` result (0) |
| `r0-160-counter-re.md` §0.5, §4 T2 | `policy+0x34 == 0` is the veto that wedged boots 2/3 | **Falsified** by T2 (`pol34 = 1` at the wedge) and by completing jobs with `pol34 = 0` |
| same §0.5 | `policy+0x34` "stays zeroed forever" at `policy+4 = 0` | **False**: apple6d4 shows 0/1/0 (some caller of the sampler/setter runs at `policy4 = 0`, `6d4 = 1`); who calls the sampler there is [U] |
| `dsx-nx15` §3 | `policy+0x3c..0x19b` / `0x630..` "no firmware consumer" | The reader search only covered sites loading the policy from `root+0x70`; functions that take the policy pointer as an argument (`0x1000aa80`, `0x1000b0d8..0x1000cf38`) read `+0x630..+0x6f4` heavily (dozens of loads, [V] offsets, base register unchecked). Treat "unused" as unproven |

## 8. Open

- Which thread executes the sampler/governor, and whether a thread parked in `0x10007a94` would stop the ALIVE events (decides H2 vs the observation that they continue).
- `policy+8`, `+0xc` and the companion words during the wedge (not logged).
- The UNKNOWN encodings at `0x10003bec`/`0x1000d52c` (conditional ALU forms).
- Whether Linux's Fender actually completes a request to a state other than 1 (never observed).
- `0x10022f88` behaviour for a zero divisor (`policy+4 = 0` makes `0x1000ac28` divide by 0): it decides whether `policy+4 = 0` turns the period handler off.
- The role of `unit 6 +0x2c` (`0x1b8c0`/`0x1b908`) on the `0x1001c9b8` locked path.
