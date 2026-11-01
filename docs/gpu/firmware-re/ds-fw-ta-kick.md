# DS: the G5P firmware's TA→VDM kick path, instruction by instruction

2026-10-23. Offline only: no device access, no kernel edits, no commits.
Firmware `build/gpu/firmware-re/{80000000,10000000}.bin` (SHA256 pinned in
`tools/gpu-fw-emu/interpreter.py`), captures from
`docs/gpu/experiments/logs/r24-*` and `logs/r26/`, inputs parsed by the
bounded META interpreter in `tools/gpu-fw-emu/`. New tool:
`tools/gpu-ds/trace-ta-kick.py` (not published)
(per-instruction access attribution over the unmodified interpreter; no hooks,
no per-PC substitutions).

Read with [firmware-re/README.md](README.md) rounds 14–16,
emulator-round1.md (`emulator-round1.md`, not published),
24-ta-hardware.md (`../experiments/24-ta-hardware.md`, not published),
26-event20.md (`../experiments/26-event20.md`, not published) and
ds-sprint-1.md (`../experiments/ds-sprint-1.md`, not published). This document does not re-derive
`0xB20`, the event chain or the clock work; it answers one narrower question:
**from consuming a TA command to writing the VDM/TA start register, which
registers does the firmware write, from what, and what does it assume?**

Evidence classes used below: **[Proven]** = instruction bytes plus an executed
replay (or an exact captured word); **[Inferred]** = consistent decode without
a header or a device confirmation; **[Open]** = not decided by this evidence.

---

## 0. Bottom line, in six lines

1. The TA work is a **command record** at `rec = 0xC0254A00`, type `0x23`, consumed
   by the firmware scheduler at `80004990`. The scheduler advances a cursor,
   not a ring index; the record is Apple's "TA start" packet.
2. `start23` (`8000672c`) then executes **63 MMIO writes in a fixed order**
   (section 2) and **no writes at all after the kick**. Every value is either a
   constant or a copy of a captured `W`/`S` field (section 3).
3. The register that starts the VDM/TA hardware is **`R0+0x400 = 1`**, written
   once, at **`8000912c`**, from **one** call site (`80006808`). `0x408/0x40c`
   is the *stream base*, not the kick. `0x430` is a status register, not a
   done bit. **[Proven by uniqueness + the alternate branch]**
4. The kick is **unconditional** apart from two data tests: `*(rec+0x20)` must be
   zero (otherwise the firmware injects a software `EVENT_STATUS=0x20` instead),
   and the three PM handshakes plus the `0xcb8` handshake must self-clear.
5. The compute path (`80007390`) reaches its kick (`R0+0x478 = 1` at `80008f3c`)
   *after* pulsing `SOFT_RESET2` bit 1, writing three byte-lane context
   mappings, and uploading five engine registers plus six port qwords. **The
   TA path does none of the reset or the byte-lane mapping for CDM and never
   pulses `0x108`.** Section 6.
6. Of everything our job feeds into this path, exactly one whole consumed block
   is **all zero**: the six context qwords `W+0xd0..W+0xff` written to the TA
   context port `R0+0x11030`. That is the highest-ranked remaining input
   suspect, not a proven cause. Section 8.

---

## 1. Where a TA command is consumed

### 1.1 The execution slot and its cursor

The scheduler (`80004800`) runs per-slot records at
`0x10016510 + slot*0x2e8`. For the retained capture, slot 0:

| read | PC | value | meaning |
|---|---|---|---|
| record+0x14 | `80004890` | 1 | active (replay admission is synthetic; see emulator-round1) |
| record+0x0c | `800048ac` | 0 | pending mask |
| record+0x08 | `80004940` | 1 | running |
| record+0x10 | `80005194`/`80004954` | 0 (admission overlay) | table cursor (entry index) |
| record+0x68 | `8000497c` | **`0xC0254A00`** | **command cursor** |

`80004990 GETD D0.6,[D0.7]` loads the first command word;
`800049a4 AND D0FrT,D0.6,#0x3f` extracts the type; `SUB #1; CMP #0x32; BLS`
bounds it to 1..51 and indexes the branch chain at `800049fc`. **[Proven]**

### 1.2 The TA-start command

The retained record at `0xC0254A00` (captured as `RENDER TA-SKU`, and located by
`W+0x454` pointing at it):

```text
+0x00 00000023     type 0x23 = 35  -> dispatch entry 80004b0c -> 80004d64
+0x04 c02547a4     P: the 8-dword register table at W+0x3a4
+0x08 c0254420     Q: the 28-qword register block at W+0x20
+0x0c c025c080     S: PM selected state record
+0x10 43201be4     global state object
+0x14 c0254000     CH (channel)
+0x18 00000008     TA hardware slot (8)      <- arg+0x14
+0x1c 00000000
+0x20 00000000
+0x24 00000000     real kick vs software event selector (arg+0x20)
+0x28 00000000
+0x2c 00000000
+0x30 c0254864     runtime-flag object (arg+0x2c); *(that) == 0 -> normal path
+0x34 00000000
+0x38 00000000
+0x3c 00000000
+0x40 00000001
```

Offsets in that listing are `rec`-relative; the firmware reads them as
`arg+off` with `arg = rec+4`.

The handler body (`80004d64`):

```text
80004d64 GETD D0FrT,[A0StP-52]    ; slot record
80004d6c ADD  D1.6,D0.7,#0x44     ; next command = rec+0x44
80004d74 ADD  D1Ar1,D0.7,#0x4     ; argument = rec+4
80004d7c GETD D0Ar2,[D0FrT+16]    ; table cursor
80004d88 LSL  D0Ar2,D0Ar2,#2
80004d90 GETD D0Ar2,[D0Ar2+D0FrT+36]
80004d98 CALLR start23
```

So `start23`'s argument is **`rec+4`**, and the record's dwords are read as
`[arg+0]..[arg+0x34]`. This matters: `W` (the work object, `0xC0254400`) is *not*
the argument; `W` is reached through `rec+0x04`/`rec+0x08`, and
`W+0x454 -> rec`. **[Proven]**

Type `0x23` = 35 is the only TA-start type; `0x24` = 36 dispatches `finish24`
(`800068a4`) and `0x32` = 50 dispatches CDM start (`80007390`). **[Proven]**

---

## 2. The ordered write list

Replay: scheduler entry `80004800`, argument `0x8000a2bc`, synthetic admission
of slot 0, captured `TA-consumed` checkpoint, unknown reads zero-stubbed.
764 instructions to the `0x801` yield; the kick is instruction 548.

Reproduce:

```sh
python3 -B tools/gpu-ds/trace-ta-kick.py --entry scheduler --admit \
  --unknown zero --json build/gpu/ds/sched-trace.json \
  --events build/gpu/ds/sched-events.json --md build/gpu/ds/sched-trace.md
```

"Source" is the RAM word the stored register was loaded from, tracked through
stack round-trips. `W`=`0xC0254400`, `S`=`0xC025C080`, `rec`=`0xC0254A00`.

| # | PC | instruction | R0+off | value | source |
|---:|---|---|---|---|---|
|1|`0x10017d84`|`SETB [D0FrT],D1Ar1`|`+0xf078`|`0x8`|`arg+0x14`|
|2|`0x10017d9c`|`SETB [D0Ar4],D1Ar1`|`+0x136c`|`0x8`|`arg+0x14`|
|3|`0x10017db4`|`SETB [D0FrT],D1Ar1`|`+0x136d`|`0x8`|`arg+0x14`|
|4|`0x80007678`|`SETD [D1Ar5+#3192],D0FrT`|`+0xc78`|`0x88`|`W+0x3a4`|
|5|`0x8000768c`|`SETD [D1Ar5+#3208],D1Ar3`|`+0xc88`|`0x2`|`W+0x3a8`|
|6|`0x800076ac`|`SETD [D1Ar5+#3224],D0Ar4`|`+0xc98`|`0x3f003f`|`W+0x3ac`|
|7|`0x800076c8`|`SETD [D1Ar5+#3096],D0FrT`|`+0xc18`|`0x1001`|`W+0x3b0`|
|8|`0x80007704`|`SETD [D1Ar5+#3080],D1Ar3`|`+0xc08`|`0x10100c`|`W+0x3b4`|
|9|`0x80007738`|`SETD [D1Ar5+#3088],D0Ar4`|`+0xc10`|`0x10100c`|`W+0x3b8`|
|10|`0x80007764`|`SETD [D1Ar5+#3104],D0FrT`|`+0xc20`|`0x10`|`W+0x3bc`|
|11|`0x8000777c`|`SETD [D1Ar5+#3360],D1Ar3`|`+0xd20`|`0x0`|imm 0|
|12|`0x80007788`|`SETD [D1Ar5+#3296],D0FrT`|`+0xce0`|`0x1`|imm 1|
|13|`0x800077a8`|`SETD [D1Ar5+#3112],D1Ar1`|`+0xc28`|`0x20000`|`W+0x3c0`|
|14|`0x800077e0`|`SETL [D1Ar5+#3216],D0Re0,D1Re0`|`+0xc90`|`0x1e3ce5081e3ce508`|`W+0x20`|
|15|`0x80007808`|`SETL [D1Ar5+#3128],D0.5,D1.5`|`+0xc38`|`0x101020000`|`W+0x28`|
|16|`0x80007830`|`SETL [D1Ar5+#3136],D0.6,D1.6`|`+0xc40`|`0x101030000`|`W+0x30`|
|17|`0x80007848`|`SETL [D1Ar5+#3072],D0.7,D1.7`|`+0xc00`|`0x0`|`W+0x38`|
|18|`0x80007860`|`SETL [D1Ar5+#3200],A0.2,A1.2`|`+0xc80`|`0x88`|`W+0x40`|
|19|`0x80007878`|`SETL [D1Ar5+#1064],A0.3,A1.3`|`+0x428`|`0x100086240`|`W+0x48`|
|20|`0x800078a0`|`SETL [D1Ar5+#3264],D0.5,D1.5`|`+0xcc0`|`0x100086040`|`W+0x50`|
|21|`0x800078c8`|`SETL [D1Ar5+#1080],D0.5,D1.5`|`+0x438`|`0x3000524001002`|`W+0x58`|
|22|`0x800078f8`|`SETL [D1Ar5+#1088],D0.5,D1.5`|`+0x440`|`0x380`|`W+0x60`|
|23|`0x80007928`|`SETL [D1Ar5+#1096],D0.5,D1.5`|`+0x448`|`0x39000004820`|`W+0x68`|
|24|`0x80007958`|`SETL [D1Ar5+#1104],D0.5,D1.5`|`+0x450`|`0x3002121001002`|`W+0x70`|
|25|`0x80007988`|`SETL [D1Ar5+#1112],D0.5,D1.5`|`+0x458`|`0x6c0`|`W+0x78`|
|26|`0x800079b8`|`SETL [D1Ar5+#1120],D0.5,D1.5`|`+0x460`|`0x6d000004820`|`W+0x80`|
|27|`0x800079c8`|`SETL [D1Ar5+#3248],D0.5,D1.5`|`+0xcb0`|`0x0`|imm 0|
|28|`0x80007a00`|`SETL [D1Ar5+#1136],D0.5,D1.5`|`+0x470`|`0x100`|`W+0x88`|
|29|`0x80007a10`|`SETL [D0FrT],D0.5,D1.5`|`+0x420`|`0x0`|imm 0|
|30|`0x80007a40`|`SETL [D1Ar5+#1048],D0.5,D1.5`|`+0x418`|`0x100080000`|`W+0x90`|
|31|`0x80007a70`|`SETL [D1Ar5+#1032],D0.5,D1.5`|`+0x408`|`0x100040000`|`W+0x98`|
|32|`0x80007aa8`|`SETL [D1Ar5],D0.5,D1.5`|`+0xf058`|`0x39520000001`|`W+0xa0`|
|33|`0x80007ae0`|`SETL [D1Ar5],D0.5,D1.5`|`+0xf060`|`0x3a0`|`W+0xa8`|
|34|`0x80007b18`|`SETL [D1Ar5],D0.5,D1.5`|`+0xf068`|`0x6d120000001`|`W+0xb0`|
|35|`0x80007b50`|`SETL [D1Ar5],D0.5,D1.5`|`+0xf070`|`0x6e0`|`W+0xb8`|
|36|`0x80007b88`|`SETL [D1Ar5],D0.5,D1.5`|`+0xf038`|`0x3006524000081`|`W+0xc0`|
|37|`0x80007bc8`|`SETL [D1Ar5],D0.5,D1.5`|`+0xf040`|`0x30070`|`W+0xc8`|
|38|`0x80007bd0`|`SETD [D0FrT],D1Ar3`|`+0x11ff8`|`0x0`|imm 0 (index)|
|39|`0x80007c04`|`SETL [D0Ar4],D0.5,D1.5`|`+0x11030`|`0x0`|`W+0xd0`|
|40|`0x80007c10`|`SETD [D0FrT],D0.5`|`+0x11ff8`|`0x1`|imm 1 (index)|
|41|`0x80007c34`|`SETL [D1Ar5],D0Ar4,D1Ar3`|`+0x11030`|`0x0`|`W+0xd8`|
|42|`0x80007c3c`|`SETD [D1.5],D0FrT`|`+0x11ff8`|`0x2`|imm 2 (index)|
|43|`0x80007c78`|`SETL [D1Ar5],D0.5,D1.5`|`+0x11030`|`0x0`|`W+0xe0`|
|44|`0x80007c88`|`SETD [D0Ar4],D0FrT`|`+0x11ff8`|`0x3`|imm 3 (index)|
|45|`0x80007ca4`|`SETL [D1Ar5],D0Ar2,D1Ar1`|`+0x11030`|`0x0`|`W+0xe8`|
|46|`0x80007cac`|`SETD [D0Ar4],D0FrT`|`+0x11ff8`|`0x4`|imm 4 (index)|
|47|`0x80007ce4`|`SETL [D1Ar5],D0.5,D1.5`|`+0x11030`|`0x0`|`W+0xf0`|
|48|`0x80007cec`|`SETD [D0Ar4],D0FrT`|`+0x11ff8`|`0x5`|imm 5 (index)|
|49|`0x80007d28`|`SETL [D1Ar5],D0.5,D1.5`|`+0x11030`|`0x0`|`W+0xf8`|
|50|`0x80007d38`|`SETL [D0FrT],D0Ar4,D1Ar3`|`+0xf000`|`0x0`|imm 0|
|51|`0x80007d40`|`SETL [D1Re0],D0Ar4,D1Ar3`|`+0xf008`|`0x0`|imm 0|
|52|`0x80007d48`|`SETL [D0Re0],D0Ar4,D1Ar3`|`+0xf010`|`0x0`|imm 0|
|53|`0x80007d50`|`SETL [D0.5],D0Ar4,D1Ar3`|`+0xf018`|`0x0`|imm 0|
|54|`0x1001d56c`|`SETL [D0Ar6+#976],D0Ar4,D1Ar3`|`+0x3d0`|`0x100000100`|imm 0x100:0x1|
|55|`0x800098d8`|`SETL [D1Re0+#576],D0Ar4,D1Ar3`|`+0x240`|`0x101000000`|`S+0xa8` & `~0xf`|
|56|`0x80009910`|`SETL [D0Ar6+#584],D0.7,D1.7`|`+0x248`|`0x101010080`|`S+0xb0` & `~0xf`|
|57|`0x80009938`|`SETL [D0Ar6+#728],D0Ar2,D1Ar1`|`+0x2d8`|`0x101000080`|`S+0xa0` & `~0xf`|
|58|`0x80009940`|`SETD [D1.5],D0FrT`|`+0x290`|`0x1`|imm 1|
|59|`0x80009974`|`SETD [D0.5],D0Ar4`|`+0x198`|`0x1`|imm 1|
|60|`0x800099a8`|`SETD [D0.5],D0Ar4`|`+0x258`|`0x1`|imm 1|
|61|`0x80009150`|`SETD [D0.5],D0Ar4`|`+0xcb8`|`0x1`|imm 1|
|62|`0x80009178`|`SETD [D0Ar6+#3176],D0FrT`|`+0xc68`|`0x1`|imm 1|
|63|`0x8000912c`|`SETD [D0Ar6+#1024],D0FrT`|`+0x400`|**`0x1`**|imm 1 — **the kick**|

Two non-write events are also part of the order: `800067b4 -> 8000a874 ->
10005f30 -> 1002706c` runs before row 54 (it is the PM preparation helper), and
`800067c4` publishes `S+0=0, S+4=1, S+0xc=global` between rows 53 and 54.
**[Proven]**

### 2.1 Phases

| phase | rows | what it is |
|---|---|---|
| context mapping | 1–3 | `0xf078` byte 0 and `0x136c/0x136d` = TA slot 8 |
| W "A/B" table | 4–13 | 8 dwords from `P = W+0x3a4` into `0xc78..0xc28`, plus two constants (`0xd20=0`, `0xce0=1`) |
| TA/VDM register block | 14–37 | 22 qwords from `Q = W+0x20` into `0xc90..0xf040`, plus two constants (`0xcb0=0`, `0x420=0`) |
| TA context port | 38–49 | 6 payload qwords to `0x11030`, index 0..5 in `0x11ff8` |
| ISP zeroing | 50–53 | `0xf000/0xf008/0xf010/0xf018 = 0` (this is a TA-only job) |
| PM prepare | 54 | `0x3d0 = 0x1_00000100` (the `flag==1` arm of `1001d550`) |
| PM aux + 3 handshakes | 55–60 | `0x240/0x248/0x2d8` then `0x290`, `0x198`, `0x258` |
| pre-kick gate | 61–62 | `0xcb8 = 1` (poll for clear) then `0xc68 = 1` |
| **kick** | **63** | **`0x400 = 1`** |

Register names that are corroborated by an Imagination header are: `0x430`
`VDM_CONTEXT_STORE_STATUS`, `0x438/0x440/0x448` `VDM_CONTEXT_STORE_TASK0/1/2`,
`0x450/0x458/0x460` `VDM_CONTEXT_RESUME_TASK0/1/2`, `0xf078`
`CONTEXT_MAPPING0` (fields TA/3D/CDM/2D in bytes 0..3). The retained DDK
(`build/gpu/firmware-re/round10-rgx_cr_defs_km.h`) and the upstream
`pvr_rogue_cr_defs.h` both stop there; **`0x400`, `0x408`, `0x418`, `0x420`,
`0x428`, `0x470`, `0x480`, `0x498`, `0x4c8`, `0xcb0`–`0xd20`, `0xf000`–`0xf070`
and `0x136c` have no authoritative name in either header** and are described
here by the firmware's own use only. **[Proven for offsets/values; Inferred for
semantics]**

---

## 3. What each write depends on

### 3.1 Input structures

| symbol | address | role |
|---|---|---|
| `rec` | `0xC0254A00` | the TA-start command record (`W+0x454` points here) |
| `W` | `0xC0254400` | the work object; `rec+4` and `rec+8` point into it |
| `P` | `0xC02547A4` = `W+0x3a4` | 8-dword register table (`rec+4`) |
| `Q` | `0xC0254420` = `W+0x20` | 28-qword register block (`rec+8`) |
| `S` | `0xC025C080` | PM selected state record (`rec+0xc`) |
| `CH` | `0xC0254000` | channel (`rec+0x10`) |
| `GbP` | `0x100139C0` | firmware globals (`A1GbP`) |

### 3.2 Reads the path performs, grouped

| read | PC | value in the retained capture | feeds |
|---|---|---|---|
| `GbP+0x24` | `8000766c` etc. | `0x02000000` — the MMIO base itself | every `R0+off` |
| `GbP+0x6c` | `80006810` | `0x8000a000` control block | **control+0x170 test after the kick** |
| `GbP+0x100` | `80004f4c` | `0x8000a2c8` | timestamp helper state |
| `GbP+0x10c` | `800022cc` | `0x1001611c` | PM current-selection index table |
| `GbP+0x110` | `8000487c` | `0x10016510` | execution slot table |
| `arg+0` | `80004990`/`80006760` | `0x23` then `P` | dispatch (type), then the table pointer |
| `arg+4` | `80006768` | `Q` | the register block |
| `arg+8` | `80006794` | `S` | PM selected state |
| `arg+0xc` | `800067c4` | global | published into `S+0xc` |
| `arg+0x10` | `800067d4` | `CH` | published into `S+0xc` slot |
| `arg+0x14` | `80006734` | `8` | `0xf078`,`0x136c`,`0x136d` and the `10016318` compare |
| `arg+0x18` | `8000673c` | `0` | second argument of `10017d6c` (unused) |
| `arg+0x1c` | `8000674c` | `0` | second argument of `800022c0` |
| `arg+0x20` | `800067ec` | `0` | **real kick vs software event** |
| `arg+0x2c` | `8000678c` | `0xC0254864` | runtime flag → normal vs `80006888` branch |
| `*(arg+0x2c)` | `8000679c` | `0` | **must be 0** for the normal path |
| `arg+0x34` | `80006778` | `0` | second argument of `8000a814` |
| `P+0..0x1c` | `80007664`..`80007790` | 8 dwords | rows 4–13 |
| `Q+0..0xd8` | `800077bc`..`80007d0c` | 28 qwords | rows 14–37, 39–49 |
| `S+0xa0/a4` | `80009918`/`80009920` | `0x1_00010080` | `0x2d8` |
| `S+0xa8/ac` | `800098ac`/`800098c0` | `0x1_01000000` | `0x240` |
| `S+0xb0/b4` | `800098e8`/`800098f0` | `0x1_01010080` | `0x248` |
| `0x10016318` | `800022f0` | **unknown** (stub 0) | PM record index; choosing 0 vs anything else changes which `S` is used |
| `0x8000a170` | `80006818` | **unknown** (stub 0) | post-kick branch |
| `0x8000a2bc` | `80005d7c` | **unknown** (stub 0) | the `0x801` event test |

The `S+0xa8/b0/a0` values are a useful consistency check: round 16's constructor
arithmetic for `N=1` predicts `a8 = aux`, `a0 = aux+0x80`,
`b0 = aux+0x10080`. With `aux = 0x1_01000000` the capture is exactly
`0x1_01000000`, `0x1_01000080`, `0x1_01010080`. **Our PM state record is
self-consistent with the documented layout.** **[Proven]**

### 3.3 The `Q` block layout (W+0x20 -> registers)

`Q+k` is a straight qword copy; every entry is one 64-bit store:

| Q | W | R0+ | Q | W | R0+ |
|---|---|---|---|---|---|
|+0x00|+0x20|`0xc90`|+0x58|+0x78|`0x458`|
|+0x08|+0x28|`0xc38`|+0x60|+0x80|`0x460`|
|+0x10|+0x30|`0xc40`|+0x68|+0x88|`0x470`|
|+0x18|+0x38|`0xc00`|+0x70|+0x90|`0x418`|
|+0x20|+0x40|`0xc80`|+0x78|+0x98|`0x408`|
|+0x28|+0x48|`0x428`|+0x80|+0xa0|`0xf058`|
|+0x30|+0x50|`0xcc0`|+0x88|+0xa8|`0xf060`|
|+0x38|+0x58|`0x438`|+0x90|+0xb0|`0xf068`|
|+0x40|+0x60|`0x440`|+0x98|+0xb8|`0xf070`|
|+0x48|+0x68|`0x448`|+0xa0|+0xc0|`0xf038`|
|+0x50|+0x70|`0x450`|+0xa8|+0xc8|`0xf040`|
| | | |+0xb0..0xd8|+0xd0..0xf8|`0x11030` ×6 (index 0..5)|

The 22 block qwords and the 6 port qwords together consume `Q` exactly
(`+0x00..+0xd8` = 28 qwords), which is a useful cross-check on the decode: no
read is unaccounted for and none is read twice.

---

## 4. The waits and handshakes on the way to the kick

Four "write 1, poll until self-clear" transactions run before the kick. Each is
`SETD [x],1` then a loop through the MMIO read trampoline `8000a4d4` plus the
delay helper `8000a534`, with the predicate `(v & 1) == 0`.

| # | register | setter | poll PC | predicate | captured |
|---|---|---|---|---|---|
|1|`0x290`|`80009940`|`8000994c`/`800099f4`|`bit0 == 0`|not sampled|
|2|`0x198`|`80009974`|`80009980`/`80009a24`|`bit0 == 0`|not sampled|
|3|`0x258`|`800099a8`|`800099b4`/`80009a0c`|`bit0 == 0`|not sampled|
|4|`0xcb8`|`80009150`|`8000915c`/`80009198`|`bit0 == 0`|`cb8=0` at `TA-consumed` **[Proven]** |

`0x240/0x248/0x2d8` are written *before* their handshake, so each pair is
"address qword, then go". `0xc68 = 1` is written unconditionally immediately
after the `0xcb8` handshake returns (`80009178`), and nothing ever polls
`0xc68` in this path.

Consequence for the stall: **if any of `0x290/0x198/0x258/0xcb8` failed to
self-clear, the firmware would never reach row 63.** The device evidence that
`0x430` changes `1 -> 0x11` only in runs where the TA command was consumed
(ds-sprint-1 §3) argues that row 63 did execute on device. That is the strongest
available inference that the pre-kick handshakes completed; it is not a direct
sample of `0x290/0x198/0x258`. **[Inferred]**

---

## 5. Which register actually starts the VDM/TA hardware

### 5.1 `R0+0x400 = 1`, once, from one call site

`R0+0x400` is written exactly once in the whole `80000000.bin` image:

```text
80009120 GETD D0Ar6,[A1GbP+36]     ; R0
80009128 MOV  D0FrT,#1
8000912c SETD [D0Ar6+1024],D0FrT   ; R0+0x400 = 1
80009134 MOV  PC,D1RtP
```

and the only caller is `80006808`, inside `start23`, immediately after the
`S`/channel publication and immediately before the `control+0x170` test. The
`10000000.bin` image never writes `0x400`. **[Proven]**

Its sibling at the same call site makes the meaning explicit:

```text
800067ec GETD D0FrT,[D0.5+#32]     ; *(arg+0x20)
800067fc CMP  D0FrT,#0
80006800 BNE  8000687c             ; nonzero -> software event
80006808 CALLR 80009120            ; zero     -> R0+0x400 = 1
...
8000687c CALLR 80009104            ; R0+0x130 = 0x20  (EVENT_STATUS <- TA_FINISHED)
```

`80009104` writes `EVENT_STATUS`'s own `TA_FINISHED` bit. So the two arms are
"make the hardware produce `TA_FINISHED`" and "pretend it did". That is the
firmware's own statement that `0x400` is the thing that would make the event
appear. **[Proven]**

### 5.2 What `0x400` is not

* `0x408/0x40c` is the **stream base** (64-bit, `0x1_00040000 = W+0x98`, the VDM
  BO), written 32 stores earlier at `80007a70` (row 31). Reading `0x408 = 0x1_00040000`
  back proves the pointer was programmed; it says nothing about execution.
* `0x430` is `VDM_CONTEXT_STORE_STATUS` (`COMPLETE` bit 0, `NEED_RESUME` bit 1,
  `LAST_PIPE` bits 4–7). Its bit 0 is already 1 *before* launch and reads `0x11`
  after. It is a readiness flag for the firmware's own finish path, not a
  job-done bit (r24 reached the same conclusion).
* `0xcb8`/`0xc68` are handshake/flag registers in the `0xC00` TA block; nothing
  polls `0xc68`, and `0xcb8` is a request that clears.
* `0xf008` is the **ISP/3D** start register: written by three other sites
  (`800083ac`, `80008588`, `8000946c`) with a value loaded from the 3D work, and
  the TA path deliberately writes `0` into it (rows 50–53).
* `R0+0xb00` (`MTS_SCHEDULE`) is the host→MTS dispatch and is *also* written by
  the firmware's own thread scheduler (`1001ee28/1001ee44/1001ee8c`, values
  `arg & 0xf`, `5`, `(arg & 0xf) | 0x20`). It selects MTS threads; it is not the
  engine start.

### 5.3 What happens after the kick

```text
80006810 GETD D0Ar6,[A1GbP+108]   ; control block 0x8000a000
80006818 GETD D0FrT,[D0Ar6+368]   ; *(u32*)(control+0x170)  <-- unknown on device
80006820 TST  D0FrT,#1
80006824 BNE  80006840            ; bit0 set -> tail call 8000a434 -> 10017688
```

with bit 0 clear, `start23` returns; the scheduler advances to `rec+0x44`
(`0x80000018`, type 24, handler `80004ef0`), which reads `R0+0x160/0x164`
(`TIMER`) and then moves the cursor to `0xC0254A58` holding `0x00000801` — the
**event wait**, type 1, mask
`0x801 >> 6 = 0x20`, tested against private `0x8000a2bc`. **The firmware never
polls any TA status register between the kick and the event wait.** There is no
"did the engine accept it" acknowledgement to miss. **[Proven]**

---

## 6. The compute (CDM) path that works

CDM start is `80007390`, dispatched by command type `0x32` at `80004bfc`. Its
sequence is code-derived (no CDM capture exists); the five-register block was
additionally confirmed by executing `80008d24` with a scratch work object.

```text
80007390 start(W):
  80007398  CALLR 8000a714 -> 1001e7b8
            1001e7d4   R0+0x108:qword = (0x2, 0x0)
            1001e7e0   poll 10027200(R0+0x108) until (v & 2) == 2
            1001e820   R0+0x108:qword = 0
            1001e82c   poll until (v & 2) == 0
  800073a0  D1Ar1 = *(W+0x10); D0Ar2 = *(W+0x14)
  800073b0  CALLR 8000a704 -> 1001838c
            100183a4   R0+0xf07a (byte) = slot & 0xff   ; CONTEXT_MAPPING0.CDM
            100183bc   R0+0x136e (byte) = slot & 0xff
            100183d4   R0+0x136f (byte) = slot & 0xff
  800073b8  D1Ar1 = *(W+0), D0Ar2 = *(W+4)
  800073c8  CALLR 80008d24  (P, Q):
            80008d40   R0+0x4c0        = P+0x0
            80008d54   R0+0x4c8        = P+0x4
            80008d7c   R0+0x498:qword  = Q+0x00
            80008dc0   R0+0x4b8:qword  = Q+0x08
            80008dd8   R0+0x480:qword  = Q+0x10
            80008de0   R0+0x11ff8      = 0
            80008e0c   R0+0x11040:qword= Q+0x18   (index 1..5 likewise, Q+0x20..0x40)
  800073d0  D1.5 = *(W+0x20)
  800073d8  if *(D1.5) == 0:
  800073f4    CALLR 80008f30  ->  R0+0x478 = 1        <-- CDM kick
  80007400    CALLR 8000a6f4 -> 10018358
              10018378  R0+0x4a8:qword = *(D1.5+0x34):(D1.5+0x38)  ; CDM_CONTEXT_PDS0
              10018380  R0+0x4b0       = *(D1.5+0x3c)              ; CDM_CONTEXT_PDS1
```

### 6.1 Differences that matter

| aspect | TA/VDM (`start23`) | CDM (`start`) |
|---|---|---|
| block reset | **none** | `0x108` bit 1 pulsed, with two polls |
| context-mapping write | `0xf078` **byte 0** + `0x136c`,`0x136d` = slot | `0xf07a` **byte 2** + `0x136e`,`0x136f` = slot |
| engine registers | 30 driven by `P`/`Q` (8 dwords + 22 qwords) across `0xc00..0xd20`, `0x408..0x470`, `0xf000..0xf070`, plus 4 constants | 5 (`0x480`, `0x498`, `0x4b8`, `0x4c0`, `0x4c8`) |
| context payload port | **`0x11030`** (qword) ×6, index in `0x11ff8` | **`0x11040`** (qword) ×6, index in `0x11ff8` |
| engine kick | `0x400 = 1` | `0x478 = 1` |
| post-kick | publish `S`/channel, test `control+0x170` | write `0x4a8`/`0x4b0` from `*(W+0x20)` |
| PM transactions inline | **3 loads + `0xcb8` gate** | **none** |
| ISP/3D writes | clears `0xf000..0xf018` | none |

Two of those are structurally interesting and none of them is host-controlled:

1. **The reset pulse exists only for CDM.** `1001e7b8` is called from exactly
   one site (`80007398`), and `#0x108` appears in only one other helper
   (`1001a038`, the boot reset). If the VDM/TA block expects a reset before its
   first job and only receives one incidentally from an earlier CDM/3D job,
   then a TA-first epoch is a genuinely different hardware state from a
   compute-first epoch. **[Inferred — no device evidence either way]**
2. **The TA path is the only one that performs PM loads inline**, so it is the
   only engine whose start can be blocked by a PM handshake.

The byte-lane difference (`0xf078` vs `0xf07a`) is *not* a bug: `CONTEXT_MAPPING0`
has TA in byte 0, 3D in byte 1, CDM in byte 2, 2D in byte 3, so each path writes
its own field. `0x136c` is unnamed; the two paths write bytes 0/1 and 2/3 of it
respectively, which is consistent with a second mapping register with 16-bit
TA and CDM fields. **[Inferred]**

---

## 7. What our job feeds into these writes

From `docs/gpu/experiments/logs/r24-*` (`TA-W`, `TA-SKU`, `PM-control-state`) and
the r26 analysis files. The 38 direct dwords that the observer compares are
identical between the device and this replay, so the upload **transport** is not
in question; what follows is only about values.

| register(s) | value | provenance | comment |
|---|---|---|---|
|`0xf078`,`0x136c`,`0x136d`|`8`|`arg+0x14`|TA slot 8, matches `CONTEXT_MAPPING0.TA` semantics|
|`0xc78`,`0xc80`|`0x88`|`W+0x3a4`,`W+0x40`|two identical constants|
|`0xc88`|`2`|`W+0x3a8`| |
|`0xc98`|`0x003f003f`|`W+0x3ac`|two 0x3f fields|
|`0xc18`|`0x1001`|`W+0x3b0`| |
|`0xc08`,`0xc10`|`0x0010100c`|`W+0x3b4`,`W+0x3b8`|identical pair|
|`0xc20`|`0x10`|`W+0x3bc`| |
|`0xc28`|`0x20000`|`W+0x3c0`|bit 0 clear|
|`0xc00`|`0`|`W+0x38`| |
|`0xc90`|`0x1e3ce5081e3ce508`|`W+0x20`|identical halves|
|`0xc38`,`0xc40`|`0x1_01020000`,`0x1_01030000`|`W+0x28`,`W+0x30`|RT main / secondary GPU VAs|
|`0x428`,`0xcc0`|`0x1_00086240`,`0x1_00086040`|`W+0x48`,`W+0x50`|inside the `0x1_00080000` scratch (0x8000 bytes)|
|`0x418`,`0x408`|`0x1_00080000`,`0x1_00040000`|`W+0x90`,`W+0x98`|scratch BO / **VDM stream base**|
|`0x438..0x460`|see rows 21–26|`W+0x58..0x80`|VDM context store/resume task words|
|`0x470`|`0x100`|`W+0x88`| |
|`0x420`,`0xcb0`,`0xd20`,`0xf000..0xf018`|`0`|immediate|deliberate disable/zero|
|`0x240`,`0x248`,`0x2d8`|`0x1_01000000`,`0x1_01010080`,`0x1_01000080`|`S+0xa8/b0/a0`|matches the round-16 constructor arithmetic exactly|
|`0x3d0`|`0x1_00000100`|immediate|`(0x100, 0x1)` — size/base-shaped|
|**`0x11030` ×6**|**`0`,`0`,`0`,`0`,`0`,`0`**|**`W+0xd0..0xf8`**|**the whole six-qword context payload is zero**|
|`0x400`|`1`|immediate|the kick|

### 7.1 The all-zero block

`W+0xcc .. W+0x3a0` is 728 zero bytes in the captured object, and the values the
TA context port receives (`W+0xd0..W+0xff`) are inside it. The same object has
real data at `W+0x3a4..0x3c8` (`P`), `W+0x444..0x488` and `W+0x514..0x544`, so
this is not a capture-truncation artefact: the kernel fixture genuinely leaves
that region zero.

The **CDM** path uploads the identical shape — six qwords, index 0..5 in
`0x11ff8` — from its own `Q+0x18..0x40`. Its values are unknown to us (no CDM
capture exists), so this document cannot claim that a nonzero payload is
required. It is reported because it is the largest input the kick path consumes
whose content our job does not author.

---

## 8. Ranked: input values in our job that could leave the VDM/TA idle

Ranking is by *how much of the idle outcome the value could explain* weighted by
*how cheap the discriminator is*. Nothing here is a proved cause; the retained
evidence already demotes the clock, PM-pool, slot-order, terminate-only and
longer-wait families, so they are not repeated.

| # | candidate | why it could idle the engine | cheapest discriminator |
|---|---|---|---|
|1|**`W+0xd0..0xff` = 0** (TA context port `0x11030`, indices 0..5)|The six qwords are written immediately before the kick to a dedicated data port plus index register. All-zero means the engine is handed an empty/NULL parameter-context block. This is the only consumed input our job leaves entirely blank.|Read `R0+0x11030`/`0x11ff8` back at `TA-consumed` (observer), or run the same fixture through the *compute* path and capture the CDM `Q+0x18..0x40` values for comparison.|
|2|**`0x438/0x440/0x448/0x450/0x458/0x460`** (VDM context store/resume task0..2)|DDK-confirmed context-load descriptors. If the TA must load a context before fetching, a wrong/unmapped descriptor stops it before the first stream word, with no event and no fault the host can see.|Read `0x430` after the kick: `NEED_RESUME` (bit 1) set would mean the engine wants a context it does not have. Also verify every address inside those packed words is mapped in slot 8.|
|3|**`0x408/0x40c = 0x1_00040000` + slot-8 translation**|The value is right (r24), so the residual is CBASE/MMU state for slot 8 and the VDM BO's content. A stream that cannot be fetched, or that is all-zero (type-0 PPP, count 0), never reaches the terminate block and never signals.|V2 "poison the VDM BO" (ds-sprint-1 §4) already separates "never fetches" from "fetches the wrong bytes"; also read slot 8's CBASE after `DC3`.|
|4|**`0xc18 = 0x1001`, `0xc08/0xc10 = 0x0010100c`, `0xc28 = 0x20000`, `0xc20 = 0x10`**|The `0xC00` block is the largest per-job configuration the TA receives. Any of these could be a size/enable/PDS field whose zero or wrong width is not checked by firmware.|Diff against a second, different workload (full VDM) at the same stage: fields that do not move are structural, fields that do are suspects.|
|5|**`0x420 = 0` (64-bit) and `0xcb0 = 0`**|Both are written as zero by `start23` itself from immediates. A zero base address is a legitimate disable *or* an unfilled field; the firmware cannot tell the difference and does not test it.|Read `0x420`/`0xcb0` back and compare with the 3D path, which writes a real pointer into the corresponding slot.|
|6|**PM aux `0x240/0x248/0x2d8`** + the three handshakes|If the aux pages were unmapped in the engine VM, the PM loads would fault. The handshakes complete, so this is largely excluded — but the *shadow* is that the engine VM must map the aux pages, which is host state outside `W`.|Confirm the aux GPU VAs are in slot 8's page tables (not only in the kernel's).|
|7|**`0x3d0 = 0x1_00000100`**|A 64-bit (size, base)-shaped value written by the PM helper. If the base `0x1_00000000` window is not mapped for the MTS, the subsequent PM work is meaningless.|Read `0x3d0` after the kick; check the `0x1_0000_0000` window in the engine mappings.|
|8|**`0xf058..0xf070`, `0xf038`, `0xf040`**|Written by the *TA* path but in the ISP block domain (`0xf008` is `ISP_RENDER`). If these are shared MTS/ISP context pointers, a stale value could matter.|Compare with the 3D path's `0xf0xx` writes at the same stage.|

Deliberately **not** ranked, because the retained evidence already demotes them:
geometry clock fields (r26-02/03/04 changed nothing), PM pool size/extent/F-order
(r25-04..08), TA slot 7↔8 (r25-03), the `0x801` mask alone (r26 case22 retires
with no event), and "wait longer" (stamps/CCB frozen, not slow).

### 8.1 What the host cannot fix by writing a register

No host MMIO write is missing on this path. The firmware owns `0x400`, `0x478`,
`0xf008`, the PM handshakes and `EVENT_CLEAR`; the host's only dispatch lever is
`MTS_SCHEDULE` (`0xb00`), and the firmware demonstrably received the work
(the command record was consumed, `0x430` moved, `cb8`/`c68` were reached). A
kernel patch that writes `0x400` from the host would duplicate a firmware store
and is not supported by this trace.

---

## 9. Preconditions the firmware checks, and ones it only assumes

**Checks (branches on a value):**

| precondition | site | predicate | our job |
|---|---|---|---|
|command type in 1..51|`800049b0`|`type-1 <= 0x32`|`0x23` ok|
|runtime flag clear|`800067a8`|`*(arg+0x2c) == 0`|`0` ok|
|real-kick arm selected|`80006800`|`*(arg+0x20) == 0`|`0` ok|
|PM load 1..3 accepted|`8000994c`/`80009980`/`800099b4`|`(v & 1) == 0`|not sampled; completion inferred|
|`0xcb8` accepted|`8000915c`|`(v & 1) == 0`|`cb8 = 0` **ok**|
|post-kick instrumentation off|`80006824`|`(control+0x170 & 1) == 0`|**unknown**|
|event delivered|`80005d90`|`(private & 0x20) == 0x20`|fails — the stall|

**Assumes without checking:**

* that the `0xc00`/`0x408`/`0x438` block describes a context the engine can
  resolve — no readback, no error bit;
* that the six context qwords at `0x11030` are meaningful — the store sequence
  has no completion poll;
* that `0x430`'s state is consistent with what was just uploaded;
* that the VDM stream at `0x408` is fetchable in the TA's context;
* that the block is out of reset. The CDM path resets it (`0x108`); the TA path
  does not.

The two unknown private words `0x8000a170` and `0x8000a2bc` remain the only
firmware-side values that decide *which* branch runs after the kick and *whether*
the wait is satisfied. `0x8000a170` bit 0 selects a tail call into
`8000a434 -> 10017688` instead of returning to the scheduler; that is the
`start23 -> instrumentation` path r24 already flagged. It cannot be resolved
from these captures.

---

## 10. Reproduce, and limits

```sh
# 63-write TA start path from the real scheduler entry (764 instructions to the yield)
python3 -B tools/gpu-ds/trace-ta-kick.py --entry scheduler --admit --unknown zero \
  --json build/gpu/ds/sched-trace.json --events build/gpu/ds/sched-events.json \
  --md build/gpu/ds/sched-trace.md

# start23 alone, in isolation (449 instructions, 2 unknown reads)
python3 -B tools/gpu-ds/trace-ta-kick.py --entry start --unknown zero \
  --json build/gpu/ds/ta-start-trace.json

# the CDM five-register block with a scratch work object (structure only)
python3 -B tools/gpu-ds/trace-ta-kick.py --entry 0x80008d24 \
  --arg 0x50000000 --arg 0 --arg 0 --arg 0x50000100 --map 0x50000000=0x2000 --unknown zero

# unchanged regression suite
sh scripts/gpu/firmware-re/emulator-round1.sh
```

Limits, stated so they are not read past:

* **Conditional emulation, not a device PC trace.** Synthetic zeroed ABI, private
  stack, one CPU-visible checkpoint; `--unknown zero` explicitly stubs
  `10016318`, `8000a170`, `8000a2bc`, `8000a2c8/d0/d8`. Which PM record is
  selected, and which post-kick branch runs, are therefore *assumptions*.
* **No GPU.** The TA, the MTS and the VDM perform no work in this model; MMIO
  reads are frozen samples and writes never read back. Nothing here can show
  whether the hardware started.
* **No CDM capture exists.** Section 6 is code-derived plus a scratch-executed
  block; its *values* are not comparable, only its *shape*.
* **Register semantics beyond the two Imagination headers are inferred from
  firmware use.** `0x400`, `0x408`, `0x418`, `0x420`, `0x428`, `0x470`, `0x480`,
  `0x498`, `0x4c8`, `0xcb0`–`0xd20`, `0xf000`–`0xf070`, `0x11030`, `0x11ff8`
  and `0x136c` have no authoritative name; "kick", "stream base" and "context
  port" are this document's labels, justified by the surrounding code and by
  uniqueness of the write, not by a TRM.
* **`0x430`'s `LAST_PIPE` change is not attributed here.** Whether it is caused
  by the `0x400` store or by an earlier context operation is still open.
