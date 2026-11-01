# AGXG5P bring-up — Round 2 evidence, 20H350 j71t

**Round 3 update:** experiment 02 passed on the device; G1 is achieved with
legacy BVNC 9.15.6.2. The new G2 diagnostic implements the mapping/init/boot
stages below. It has not run on the device. See the
round-3 report (`round3-report.md`, not published) and
experiment 03 (`experiments/03-boot-meta.md`, not published). Earlier round-2 implementation
limits in this document are historical; the remaining round-3 uncertainties
are called out explicitly in the appended section.

**Verified scope:** offline ARM64 reverse engineering, shared Ghidra MCP plus
an auxiliary Ghidra project for stripped virtual methods the shared analysis
did not discover. All VAs are unslid addresses in the SHA256-pinned
kernelcache from README.md (`README.md`, not published). No device access by this agent.
**Verified device evidence supplied by the orchestrator:** experiment 01
reached gfx ACTIVE but its first bare RGX read wedged. **G2 is not achieved.**
The built diagnostic now has the recovered Fender prerequisite and a real
C0 RAM image packer; firmware execution remains blocked on the complete
GPU mappings and init objects. Experiment 02 (`experiments/02-fender-meta.md`, not published)
separates these stages explicitly.

## Evidence tools and naming

**Inferred naming:** method names without a matching assertion or original
symbol are descriptive labels. Verified claims below concern the cited
operations/offsets, not recovered C++ type declarations. “FIFO” describes
the observed appended register-init stream; its internal consumer is not
separately reverse-engineered here.

**Verified:** the shared program has 90,131 functions, correct input hash,
and all relevant bytes, but `decompile_function_by_address` returns
`FUNCTION_NOT_FOUND` for several virtual entry points, including
`...6c93a9c`, `...6c9f1bc`, `...6ca43b0`. This is incomplete function
recovery, not a broken disassembler. Existing shared functions/data were
renamed through MCP; the mapping routine has a shared disassembly comment.
`scripts/gpu/capture-ghidra.sh` saves calls under `build/gpu/ghidra/`;
`tools/gpu/annotate_ghidra.py` reproduces the annotations without opening
or restarting the shared project.

**Verified:** [Siguza/iometa](https://github.com/Siguza/iometa) at
`2af3602e7edb72f478aa8958a6386aba846a89de` recovers the actual kernelcache's
class tables. Reproduce with `scripts/gpu/dump-iokit-tables.sh`.
`tools/gpu/ghidra_offline.py` imports the extracted AGX Mach-O into the
separate `build/gpu/ghidra-aux/` project, defines direct-call/vtable entry
points, analyzes, names understood operations, and saves decompilations in
`build/gpu/ghidra-aux-results/<VA>.json`. This avoids the shared project lock.
The iOS 12 symbol map is only a naming lead: it has major AGX method-count
mismatches and was **not** bulk-imported as current symbols.

## Power and the first access: the decisive correction

**Verified mapping:** the actual IOService table gives virtual `+0x3c0 =
getDeviceMemoryCount`, `+0x3c8 = getDeviceMemoryWithIndex`, `+0x3d0 =
mapDeviceMemoryWithIndex`. `AGXG5P_mapPowerAndStartFender`
**`0xfffffff006c93a9c`** switches on the count. Its **two-range** branch
(`...93d78`) selects RGX index 0, Fender index 1. The old jump table at
`0xfffffff005941115` independently agrees.

| Verified bank | ADT / ARM physical | Proof |
|---|---|---|
| R0: RGX | `0x201000000`, 1 MiB | two-range branch; global `...7b3f408` → map `...7b3f420` → accelerator `+0x2d8` at `...6c94034` |
| R1: Fender | `0x201e00000`, 1 MiB | index 1 → global `...7b3f410` → map `...7b3f428`; C0 getter `...6ca8680` |
| gfx domain | PMGR `0x20e080340`, ADT gate 124 | Linux `s800-0-3-pmgr.dtsi:ps_gfx`; both clock/power arrays contain ID 124 |

**Verified host order before first RGX read:**

1. `...6c93f78..93f90` calls provider virtual `+0x568` with `(1,0)`.
   Actual target `AppleARMIODevice ...59bbdac` indexes its clock-gate array
   `+0xa8`, then delegates to `AppleS8000IO ...642c92c`.
2. `...6c93f94..93fac` calls provider virtual `+0x570` with
   `(1,&state,0)`. Target `...59bbe58` indexes power-gate array `+0xb8`,
   delegates to `AppleS8000IO ...642c9b8`. **Inferred semantic names:**
   enableDeviceClock / enableDevicePower, corroborated by their argument
   lists, ADT gate arrays and old class map. These are separate interfaces;
   Linux's gfx genpd is the available PMGR ACTIVE consumer.
3. C0 virtual `+0xad8`, `...6ca8bb8`, returns 1, enabling Fender startup.
   j71t has two ranges and no `has-kf` property. In that path the host does:

| Order | Verified instruction VA | Bank / width | Operation |
|---|---|---|---|
| 1 | `...6c93fe8` | R1, u32 | `+0x10000 = 0xffff0000` (init FIFO terminator) |
| 2 | `...6c93ff0` | R1, u32 | `+0 = 0x40000000` |
| 3 | `...6c93ff8` | R1, u32 | `+0x1000 = 0x11` |
| 4 | `...6c93ffc..94000` | R1, u32 | poll `+0x1000` bit4 clear |
| 5 | `...6c94004..94008` | host | `IODelay(10)` |
| 6 | `...6ca6450`, `...6ca6454` | R0, u32 | read `+0x20`, then `+0x18` in `readChipInfo ...6ca644c` |

**Verified host check, not hardware measurement:** `readChipInfo` accepts
`revision & 0xff0000 == 0x90000` and version bytes 7, 10, 13, 15; it reads
N from ID bits15:8. **Inferred:** the wrapper sequence is the missing
prerequisite for experiment 01. Only a real run can confirm it on Linux.
A timeout cannot bound one non-returning MMIO transaction; experiment 02
never performs the previous bare first read and keeps wrapper and ID stages
separate. No numeric device BVNC has been measured yet.

## Fender register-init stream

**Verified Ghidra functions and object offsets:** `AGXMetaFender` object
`+0x10` is R1's CPU pointer; `+0x20` is a byte cursor. Its vtable starts at
`0xfffffff006ff6290`, allocated by `...6ca5ecc`, initialized by `...6ca64bc`.

| Verified function | VA | Operation |
|---|---|---|
| resetInitCursor | `...6ca67ac` | cursor = `0x10000` |
| advanceInitCursor | `...6ca67b8` | cursor += 4 |
| terminateInitFIFO | `...6ca67c8` | write `0xffff0000` to R1 + cursor |
| appendReg32 | `...6ca67e0` | append `(reg & 0xff00ffff) | ((flags & 0xff) << 16)`, then value |
| appendReg64 | `...6ca683c` | append `reg+4`, high32, then flagged reg, low32 |
| setGPCPerformanceState | `...6ca4ccc` | wait R1 `+0x1000` bit4 clear, write `state | 0x10` |
| setGPUCorePower | `...6ca6544` | R1 `+0x20000 = enable ? 0x20 : 0`; readback; when enabling poll bit5 set there, then bit4 clear at `+0x1000` |

**Verified important distinction:** G5 init-register setters
`...6ca3d84` (u32), `...6ca3dc0` (u64), `...6ca3dfc` (shifted physical
address) route writes through this FIFO in Fender mode; the fallback path
writes RGX directly. `setInitReg64PA` shifts the supplied address by its
shift argument; its Fender call uses flags `0xff`. Treating every call as
an immediate CPU write to R0 would reverse the startup order incorrectly.
Exact meanings of all FIFO flag bits remain **inferred/open**.

## Reset, SLC, MMU and META master boot

**Verified ordered host recipe:** `AGXMetaFirmware_prepareBoot`
**`...6c9feb4`** sets Fender control R1 `+0x10` through vtable `+0x20(3)`,
writes R1 `+0=0x40000000` (`...6ca6530`), resets the FIFO cursor, and emits:

| Verified order | RGX logical offset / width | Value / source | Evidence |
|---|---|---|---|
| 1 | `SOFT_RESET +0x100`, u64 | `0x00e7fffffffffc1d` | `...6c9feb4` |
| 2 | same | `0x00e7fe071ffffc1d` | same |
| 3 | same | `0x0000000100000000` (META/Garten held) | same |
| 4 | `META_BOOT +0xbf8`, u32 | 1, master boot | same; public META_BOOT definition agrees |
| 5 | `MMU_CBASE_MAPPING_CONTEXT +0xe140`, u32 | context 0 | `configureMMUForBoot ...6ca4e10` |
| 6 | `MMU_CBASE_MAPPING +0xe148`, u64 | catalogue physical address >> 12 | same → shifted setter `...6ca3dfc` |
| 7 | `SLC3_CTRL_MISC +0xe200`, u64 | 3 | `populateInitSequenceFirmware ...6ca4e70` |
| 8 | `SLC3_SCRAMBLE +0xe208`, u64 | `0xb42dd24b4bd22db4` | same |
| 9 | `SLC3_SCRAMBLE2 +0xe210`, u64 | same | same |
| 10 | R1 `+0x2010`, u32; `+0x2014`, u32 | `0xbf`; `0x233` | same, direct wrapper accesses; meanings open |
| 11 | RGX `+0xe160`, u64 | value from GART virtual `+0xf0` | same; not a recovered numeric constant |
| 12 | RGX `+0xbb8`, u64 | 0 | same |
| 13 | `SOFT_RESET +0x100`, u64 | 0 in Fender path | `...6c9feb4` |
| 14 | R1 init FIFO | terminator `0xffff0000` | same → `...6ca67c8` |

**Verified public comparison:** the sampled Linux header names `+0xe140`,
`+0xe148`, `+0xe200`, `+0xe208`, `+0xe210` as the MMU and SLC3 registers
above, and `+0x8328` as `JONES_IDLE`. Apple is using the SLC3/MMU register
family; do not substitute the older `+0x3828` SLC sequence from Round 1.
**Verified further GART fragment:** `...6cac314` installs PTEs using
geometry/masks stored in the GART object and virtual `+0x180` for flags.
That method, `...6ca9a60`, returns `(flags & 0xc) | ((flags & 1) << 1) |
((flags & 0x10) >> 4) << 62 | 1`. Bit0 is always set; assigning all other
bits protection/cache meanings remains **inferred**. Page-table geometry,
physical-address placement, catalogue/PDE encodings and coherency still need
completion before the Linux builder is usable.

**Verified start/wait:** `bootFirmwareAndWait ...6c9f954` in Fender mode
calls `setGPUCorePower(false)` then `setGPCPerformanceState(1)`, and waits
in `...6ca3400` for host alive flag `+0xcad`. It does not program the LDR
thread PC/SP by the simplistic direct-slave-port replay proposed initially.
The complete FIFO, GPU backing and firmware init data must exist first.

## LDR loading and the missing init objects

**Verified correction to the proposed loader:** `layoutAndLoadLDR`
**`...6c9f1bc`** computes four segment extents plus a local-memory staging
area, allocates/maps GPU memory, copies LoadMem into that allocation,
zeros ordinary segments, appends Config register/value pairs to the boot
config, and retains the thread-1 entry words. It is not a slave-port image
upload. The raw LDR secondary-loader header alone is not a boot policy.

**Verified algorithm specialized to the diagnostic's 16 KiB host page size**, used by its
packer and checked independently against every LDR record. The static kernelcache
PAGE_SHIFT variable is runtime-initialized (zero on disk), so this is not a
captured iOS runtime page-size measurement:

| LDR region | Normalized META base | Rounded size | Offset in staged RAM |
|---|---|---|---|
| T0 code | `0x10000000` | `0x18000` | `0x4000` |
| T1 code | `0x20000000` | `0x4000` | `0x1c000` |
| startup payload at `0xc0000000` | `0x40000000` | `0x4000` | 0 |
| local/alias at `0xe0001000` | `0x60001000` | `0x4000` | `0x20000` |
| special LoadMem from `0x80000080` | transient local-memory image | `0x55dc` bytes | `0x24000` |

**Verified:** these conditional 16 KiB calculations round allocation to `0x30000`; Apple skips the local
`0x8000....` ZeroMem records in this CPU copy path. Boot config starts at
allocation `+0x200`. G5 appends segment configuration for segments 0, 1,
2, 3 before the 27 LDR Config pairs; segment 2 has a special single-register
form. `appendSegmentMMU ...6ca43b0` emits base/limit/OUTA0/OUTA1 for the
others, with flags `0xf02` on base and `0x300000` on OUTA1. Segment 2 emits
only `0x0485002c = gpu_address_high | 0x100`. Exact GPU VA/backing must be
provided; META addresses must never be mistaken for ARM physical addresses.

**Verified host/shared fragments** (host members and shared offsets are
kept distinct):

| Host member / shared offset | Verified meaning / operation | Evidence |
|---|---|---|
| object `+0x220`; GPU address member `+0x280` | first init block; begins with ring pointer tables; metadata includes local-image fields at `+0x480..48c` | `...6c95da0`, `...6c96be8`, `...6c9f1bc` |
| object `+0x268` | CPU pointer to META/device init data | `...6c9edcc` |
| init `+0x630 + 0x10*i`, i=0..8 | u64 physical MMIO base, u32 META-visible alias, u32 span; slot3 is RGX, alias 0 there in this PIO path | `...6c96be8`, `...6c9edcc` |
| init `+0x6c0`, `+0x6c8` | Fender-enabled / PIO-supported flag values | `...6c96be8` |
| init `+0x758`, `+0x75c` | software-IRQ ack offset within mapped control bank and ack mask | `...6c96be8` |
| init `+0x764`, u32; `+0x768`, u32 | T1 PC `0x20000000`, SP `0x8000b3c8`, packed by swapping the StartThreads SP/PC words | `...6c9f1bc` → `...6c9edcc` |
| init `+0x774`, u64 | firmware GPU base `0xe1c0000000` | `...6c9edcc` |
| init `+0x77c`, u32; `+0x780`, u32 | G5 override values `0x00300000`, 0 (not BVNC) | `...6ca3ccc`, constant `...5940c28` |
| image `+0x3ff8` / `+0x3ffc` | two converted init pointers: first init block and shared control object; their pointer is appended after the boot-config terminator | `...6c9f1bc` |
| object `+0x2f0`; GPU address `+0x2f8` | second init/control object; `+0x1c` can request recovery inspection | allocation `...6c95da0`, `...6ca0890` |

**Verified address conversion:** `...6ca0744` maps GPU addresses to u32 META
aliases using `(low32(gpu) + 0x40000000) | 0xc0000000` normally, or an
alternate `0x40000000` window under its flag/range test. The special GPU
interval is `0xe1c3200000..0xe1c41fffff`. This is an observed conversion,
not permission to pick arbitrary GPU VAs without the GART mappings.

**Open:** complete required fields/allocations for both init objects,
firmware VA allocation, complete page-table geometry/encodings, coherency policy and
which zero/default fields the firmware accepts. Constructing partially
understood structures and calling them boot-ready would hide the main G2
risk. The diagnostic stages a pinned image and leaves execution blocked.

## Host ↔ firmware ring, kick and IRQ

**Verified:** device-control submit helper `...6c98234` uses host object
`+0x360` → control, `+0x368` → entries; consumer u32 control `+0`, producer
u32 `+0x10`; 256 slots × 20 bytes; one reserved slot; `dmb ish` before
publishing producer. `submitDeviceControl ...6ca05d4` waits for boot if
needed, enqueues, handles Fender wake state, then kicks RGX `+0xb00=0x10`.
`kickFirmware ...6ca04b0` has the same final MTS write. This remains Apple's
AGFI ABI, not a stock IMG KCCB.

**Verified:** firmware event drain `...6c99788` uses object `+0x380` →
control, `+0x3a0` → data, the same `0/0x10` indices, and **256 × 44-byte**
entries. It dispatches type0 to controller event handler
**`...6ca263c`**, which, on the first boot event, saves entry u32 `+4` to
host object `+0xcb0`, sets flags `+0x210` and **alive `+0xcad=1`**, then
wakes the command-gate sleeper used by `...6ca3400`.
**Inferred semantic name:** event0 is the firmware boot/ready notification;
the host's alive transition makes it a stronger test than a static PC.
The meaning of event0's second word is not yet established.

**Verified IRQ:** `meta-sw-interrupt` in the actual j71t ADT is a 20-byte
`<QQI>` tuple: **set `0x20e104014`, ack `0x20e104094`, mask `0x4000`**.
The host consumes this property at `...6c80380..803ec`.
`ackFirmwareIRQ ...6ca28d8` writes the supplied ack address through the
mapped control-bank PIO in Fender mode; its non-PIO fallback writes RGX
`+0xac8=0`. `handleInterrupt ...6c98ab4` routes indices 1..3 to Fender;
index4 reaches the firmware drains (index0 has an additional conditional
path). The tuple's word/bit corresponds to **AIC IRQ 174** (5×32+14), also
the fifth ADT GPU IRQ. Linux should use its IRQ controller API rather than
copy raw AIC ack writes into this platform driver.

**Verified signal for a future full G2 build:** capture event producer
change, raw 44-byte type0 entry and IRQ174 activity, then consume/ack in the
observed order. Polling IRQSTATUS or observing ENABLE=1 alone does not prove
Apple firmware initialized. `META FW started! (%s)` at
`...555ede4c` is inside the META LDR; shared Ghidra finds no ARM xref to that
string. It is not an identified ARM logger from which callers can be walked.
Likewise `agfPollFenderReg` strings at LDR `+0x1767c/17690` are firmware code
labels; reverse the META ISA separately to locate their implementations.

## Remaining experiment gates

**Prepared, unrun:** experiment 02 (`experiments/02-fender-meta.md`, not published) tests
Fender wake → the two Apple ID reads → slave-port baseline, with firmware
hash validation and exact DMA packing as independent preflight stages.
**Not implemented:** complete firmware start, ring/IRQ consumer, or a GPU
job. Next dependencies are the GPU page-table formats and full init object
construction; first obtain a successful wrapper/ID result before expanding
the on-device transaction sequence.

## Round 3: GART geometry, attributes, init packing and staged execution

**Verified device evidence:** experiment 02 Run 1 returned revision
`00090f00`, ID `011b0602`, Fender wake status `00000001`, and a ready META
slave port; the system stayed alive. The legacy decode is **9.15.6.2**.
The earlier missing-prerequisite inference is now supported by a successful
Linux run. No firmware-alive event was requested or observed in that run.

**Verified 16 KiB GPU pages:** C0 virtual `+0x848`,
`0xfffffff006ca8818`, returns 1. Accelerator startup
`0xfffffff006c7be80` consequently asserts CPU page shift 14 and initializes
GPU globals `0xfffffff007b3ed50/58/60` from the CPU page shift/mask/size.
Their on-disk defaults are 12/`fff`/`1000`; those defaults are superseded at
runtime on this path. `AGXSharedGartTableBackingG3ThruG9` geometry initializer
`0xfffffff006ca9b34` sets these fields:

- Catalogue: 1,024 u32 entries, index `(VA >> 30) & 1023`, entry
  `(directory_PA >> 12) << 4 | 1` (equivalently PA >> 8, with 4 KiB alignment).
- Directory: 512 u64 entries, index `(VA >> 21) & 511`, entry
  `(leaf_PA >> 5) << 5 | 3`, equivalently `leaf_PA | 3`.
  Constructor bytes `+0x3c..3f = 08 05 05 15` specify entry size 8,
  encoded-address shift 5, physical-address shift 5, and VA shift 21.
  The size byte is not an address shift. `3` comes from
  `0xfffffff006ca9a4c`: `(GPU_page_shift - 12) | 1`.
- Leaf: 128 u64 entries, index `(VA >> 14) & 127`, address bits
  `PA & 0xfffffff000`; the backing itself is 4 KiB aligned. Host allocation
  rounds these small tables to complete 16 KiB pages in the diagnostic.

**Verified writers:** directory allocation/install is
`0xfffffff006cab44c`, leaf install `0xfffffff006cac314`; flag encoder
`0xfffffff006ca9a60` maps input bits as
`(flags & 0xc) | ((flags & 1)<<1) | ((flags & 0x10)<<58) | 1`.
Catalogue physical address comes from virtual `+0xf8`,
`0xfffffff006ca9a10`; firmware init writes it into its context catalogue
array. RGX `+0xe148` receives this PA shifted right 12.

**Verified critical distinction:** `0xfffffff006caa6e4` derives leaf flags
from **range attributes**, not the low mapping flags alone. It reads the
range's byte at `0xfffffff0059426c8 + one_based_range*32` and combines access
permissions before invoking the leaf writer. The 12-range table begins at
`0xfffffff0059426d8`. For the ordinary low-flags-7 allocations used here:

- Range 6: image at `0xe1c0000000`, length 2 MiB, attributes `0x10`;
  PTE flags `0x4000000000000001`.
- Range 7: shared data at `0xe1c0200000`, length 48 MiB, attributes `0x14`;
  PTE flags `0x4000000000000005`.
- Range 8: uncached init roots at `0xe1c3200000`, length 16 MiB,
  attributes `0x14`; the same leaf flags as range 7.
- Range 9: PIO at `0xe1c4200000`, length 20 MiB, attributes `0x18`;
  PTE flags `0x4000000000000009`.

**Verified open definitions corroborate bit semantics:** the pinned Linux
`pvr_rogue_mmu_defs.h` identifies bit 62 as PM/META protection, bit 3 as SLC
bypass, bit 2 as CC, bit 1 as read-only, and bit 0 as valid. Thus treating
all firmware RAM as `PA|7` would accidentally mark it read-only and omit
META protection. The implementation uses coherent DMA CPU mappings,
`dma_wmb` before publication/launch and `dma_rmb` after firmware producer
updates. The root META alias follows `0xfffffff006ca0744`'s uncached window;
image/PIO aliases use the exact host conversion. **Inferred experimental
scope:** this reproduces the recovered attributes for these ordinary boot
allocations; arbitrary user BO mappings and cache transitions are not
implemented or established.

**Verified init allocation packing:** `0xfffffff006c95da0` describes CPU/GPU
slot pairs and sizes; constants start at `0xfffffff005940b90`.
`0xfffffff006c97b6c` packs objects at 64-byte boundaries. The first group
has nine objects: `1490,990,8e0,8c0,938,28,8,78c,84` bytes; used span
`0x4244`. Its main root is at range-8 base, META init at group offset
`0x3a00`. The second group includes the `0x2058` shared control, queue
controls, 44-byte event data (`0x2c00`), other ring data, and performance
storage (`0x36000`); with the separately recovered counter/catalogue arrays
placed after it, diagnostic used span is `0x50f00`. GPU virtual addresses
may be chosen within their recovered ranges; diagnostic placements are
fixed, physical pages are independently allocated.

**Verified image config:** `0xfffffff006ca43b0` emits 104 bytes of segment
pairs. These precede the LDR's 27 config pairs at image `+0x200`; the
terminator is at `+0x340`, followed by `c0003ff8`. Image `+0x3ff8/+0x3ffc`
contain the converted first root and shared-control pointers. C0
`readChipInfo` maps measured version 15 through constants
`0xfffffff005942650` / `0xfffffff005942674` to configuration words 1 and 5;
N is 6. Common init writes those into META init `+0x704/+0x708/+0x70c`.

**Verified PIO definitions in `0xfffffff006c7fd9c`, near `...6c80758`:**
Fender physical `0x201e00000`, size `0x5000`; AIC timer physical
`0x20e100000`, size one host page; software-interrupt physical
`0x20e104000`, size one page; RGX physical `0x201000000`, size `0x20000`.
The RGX tuple's META alias remains zero, exactly as the host writer does.
The optional UVD getter is zero on this two-window unit, and the remaining
unused tuples stay zero. The diagnostic maps two Fender pages, one timer
page and one software-interrupt page into range 9. META init `+0x758` is
**set offset `0x14`**, not host ack offset `0x94`; `+0x75c=0x4000` and
`+0x760=0x1020`. Host IRQ174 acknowledgement uses `0x20e104094`.

**Implemented experimental sequence, not device-verified:** `map` allocates
only owned backing and fills tables/init/config without GPU MMIO. `arm`
runs the recovered reset/MMU/SLC FIFO with META held, registers IRQ174, and
checks reset/catalogue readbacks. `load` sets the segment MMU, verifies five
image words before RAM writes, copies the four non-core segments through
bounded META slave-port writes, and applies the 27 config pairs. The special
core payload stays in mapped RAM for the Apple bootloader. `release`
replays the complete Apple init stream, ending with reset=0 and Fender
power(false)/GPC state 1. This avoids relying on a FIFO cursor surviving the
separate arm command. The master bootloader then consumes the RAM config,
init arguments and T1 PC/SP. Manual TX register guesses are not substituted
for that host sequence.

**Verified implementation limits:** no GPU jobs, userspace UAPI, shutdown,
restart or recovery are implemented. All buffers are retained until reboot;
the driver is built-in and suppresses unbind attributes. Hardware stages
require both explicit enable parameters, ordered successful stages and the
exact measured ID. Polls, slave copy, event processing and IRQ count have
bounds, although one stalled bus transaction remains uninterruptible.

**Inferred policy defaults / open question:** dynamic policy fields written
by `0xfffffff006c974f0` and related frequency/channel configuration remain
zero in the minimal G2 init. Fixed constants from that method are populated.
Whether more policy fields are needed before the firmware emits type 0 is
not yet established. Timeout or firmware exception may identify that gate;
it must not be reported as G2 success merely because threads start.

**Verified next queue boundary:** the 20-byte device-control entries and
44-byte firmware event entries are distinct from a decoded COMPUTE job.
After G2, recover the AGFI COMPUTE command and all pointer targets first,
including channel/context, CDM/PDS stream and completion storage. The
GPU-userspace workstream's recommendation (`../gpu-userspace/feasibility.md`, not published)
is a `pvr_drm.h`-style UAPI translated to Apple firmware commands. That is a
proposed G3/G4 direction; this diagnostic adds no public submission UAPI.


## Round 4 — execute the FIFO across a Fender state transition

**Verified device evidence, supplied by the orchestrator:** experiment 03
Run 1 passed preflight/wake/id/meta/map, then returned `arm=-5`; the shell
survived. The FIFO RAM held the intended first entries, but RGX reset,
catalogue, scratch and SLC control read zero. `R0+0xbb8` remained 1 despite
the queued zero write. Raw evidence is
experiment 03 Run 1 (`experiments/03-boot-meta.md`, not published)
and `docs/test-logs/2026-10-22-gpu-regdump-after-arm.txt`. The supplied direct
32-bit write to `R0+0xe148` also read back zero; META slave control writes
worked. **Inferred:** the register-init stream was stored without executing,
or its targets were unavailable. These observations do not establish an
Apple write-protection mechanism or distinguish clock gating from reset.

### Verified Apple control flow, 20H350 kernelcache

All addresses below use the full prefix `0xfffffff006`.

| Method / address | Verified operations relevant to this failure |
|---|---|
| `AGXMetaFirmware_stopFirmwareForPowerDown`, `...c9fa10` | Enabled Fender branch at `...c9fa3c..c9fa5c`: call Fender `+0x60(0)`, then `+0x10(0)`. This writes core request 0, then requests GPC state 0. |
| `AGXMetaFirmware_prepareHardwareForBoot`, `...c9fc10` | When Fender object `+0xc` is enabled: `...c9fc44..c9fc60` calls `+0x60(1)`, then `+0x58()`. This writes **R1+0x20000=0x20**, waits for bit5 there and bit4 clear at R1+0x1000, then writes R1+0=0x40000000. The later optional slave cleanup is separately conditional. |
| `AGXMetaFirmware_prepareBoot`, `...c9feb4` | Clear R1+0x10 using `+0x20(3)`, reset R1+0, reset software FIFO cursor, append the recovered reset/MMU/SLC/META entries, and terminate. Final reset=0 is present in Apple's complete stream. |
| `AGXMetaFirmware_bootFirmwareAndWait`, `...c9f954` | `...c9f998..c9f9b8`: call `+0x60(0)` then `+0x10(1)`, i.e. R1+0x20000=0 then R1+0x1000=0x11; enter alive wait `...ca3400`. There is no additional FIFO doorbell in this examined method. |
| `AGXMetaFender_setGPCPerformanceState`, `...ca4ccc` | Poll **before** writing: R1+0x1000 bit4 clear; write `state | 0x10`, read fence, retain requested state in object+8. It does not itself verify resulting RGX writes. |
| `AGXMetaFender_setGPUCorePower`, `...ca6544` | Write R1+0x20000 to 0x20 or 0, with read fence. Only the nonzero case polls bit5 then GPC bit4. |
| `AGXMetaFender_isStableOff`, `...ca6684` | Checks R1+0x10 bit0, or GPC state 0 plus R1+0 bits31:30 equal 1. Software combines more than a not-busy bit to decide off state. |
| `AGXMetaFirmware_setPlatformPower`, `...ca0f78` | Calls platform clock/power provider `+0x568/+0x570`, updates Fender enabled flag; on power-on clears control via `+0x20(3)`. Restart path `...c9e070` explicitly calls platform power 0 then 1. |

**Verified correction to the Round 3 implementation:** its `wake` left
R1+0x1000 at 1; `arm` then requested 1 again. It never issued the pre-boot
0x20 core request, and its “FIFO complete” message only meant bit4 was
clear. FIFO packing matches Apple's `...ca67e0/...ca683c`: flags byte in
bits23:16, high half first without flags, low half with flags 0xff. The
Run-1 FIFO words agree with that encoding. No flag/address-encoding change
has been justified by the current evidence.

**Inferred revised experiment:** combine Apple's stop branch, pre-boot core
request, FIFO builder and boot branch into an explicit GPC 0 → 1 cycle.
This is a new staged Linux composition, not a captured iOS execution trace.
The diagnostic polls for state 0 before filling and state 1 after requesting
start, in addition to Apple's busy/core-ack checks. It stops on an unexpected
state rather than guessing further writes. The precise meaning of core bit5
and which transition starts the FIFO remain a device-test question. The
revision does not cycle the Linux PMGR domain or force clock-control bits.

### Clock-control comparison

**Verified reference definitions:** the pinned Linux
`build/gpu/refs/linux/pvr_rogue_cr_defs.h` has `CLK_CTRL=0x0`,
`CLK_STATUS=0x8`, **`CLK_XTPLUS_CTRL=0x80`**, and `CLK_XTPLUS_STATUS=0x88`.
For the defined two-bit controls, OFF=0, ON=1, AUTO=2. `CLK_CTRL2` in that
header is at 0xd200, so calling 0x80 CLK_CTRL2 would be misleading.

**Verified arithmetic using those reference fields, not a complete G5P
feature decode:** Run 1 gives CLK_CTRL=`a02a82000a2a2a0a` and
XTPLUS_CTRL=`00000002a8aaaaaa`. At reference shifts, BIF_TEXAS(62), BIF(40)
and SLC(16) are 2; BIF_SIDEKICK(42) is 0. XTPLUS IPF(32), COMPUTE(30),
PIXEL(28), VERTEX(26), BIF_BLACKPEARL(20), USC_SHARED(18) and GEOMETRY(16)
are 2. Some low/reserved fields differ from the public family masks.
**Inferred limit:** AUTO is a policy setting, not proof that a domain is
currently clocked; reference field names do not prove every block exists
with that identity on Apple G5P. The examined prepare/stop/boot methods and
their MMU/SLC emitters do not supply a verified additional CLK_CTRL write.
A blanket force-ON write would introduce an unverified variable. Snapshot
logs retain controls and status words before/after the revised sequence so
clock changes can be compared without that guess.

### Implemented diagnostics and bounds

**Verified source behavior:** `arm` now parks GPC at 0, issues the missing
core request 0x20, fills and compares all 41 FIFO words (including the
terminator), clears the core request and requests GPC 1. The FIFO RAM is
bounded to 128 words. Every poll logs its mask, expected value, final raw
word and return code, with a 100 ms deadline. All RGX snapshot accesses are
32-bit, including both halves of wide registers, and are logged before and
after the access. RGX reset, catalogue, scratch and SLC values are collected
and printed before any comparison; a mismatch cannot short-circuit logging
the remaining words. `armed=1` requires all four comparisons to pass.

**Verified interfaces:** `registers` sysfs is a cached snapshot and performs
no MMIO. `GPU_DIAG_PHASE=status` prints it even after `failed=1` and retains
the last boot-stage error. Explicit `snapshot` reads the fixed R1 register
list first; it only reads the fixed RGX list if R1+0x1000 low five bits are
exactly 1. Otherwise it marks RGX invalid and skips those reads. Explicit
`fifo` reads only the attempted stream, capped at R1+0x101fc, and logs raw
32-bit words. These read-only diagnostics remain available after a latched
failure; they do not permit another arm/load/release attempt.

`release` uses the same park/prepare/start cycle with the complete 45-word
stream and final reset release. Its snapshot is diagnostic only because
running firmware may immediately change power/register state. The raw
44-byte type-0 event remains the G2 acceptance gate. Individual stalled bus
transactions remain outside software timeout control. The revised sequence
has been built and modeled offline, **not run on the device**.

## Round 6 — reset-safe readback and native RAM load supersede the slave stage

**Verified device:** the Omarchy run reached reset-held R0+100=0/+104=1,
then hard-locked on R0+a18. Fender active does not imply that the META
slave port is readable. **Verified host RE:** Apple supplies segment and
LDR configuration through mapped RAM (`...6c9f1bc`, `...6ca43b0`) and its
complete Fender FIFO ends reset=0 (`...6c9feb4`). The old Linux mid-stream
slave-port load was an extra composition and is now removed.

**Implemented, unrun:** `aa8df8a460` reads only 100/104 at arm, validates the
mapped image/config on CPU at load, then releases with the complete stream
and observes RAM events. It rejects later slave sampling and skips broad
R0 snapshots after reset/release/failure. Catalogue/SLC writes are programmed,
not reported as read back. The software summary reads cached/CPU data only.
round6-readback.md (`round6-readback.md`, not published) gives exact Apple port accesses,
lifecycle predicates, public reset definitions and the inference boundary.
Experiment 03 (`experiments/03-boot-meta.md`, not published) is the current next-run plan;
earlier slave-load and broad-snapshot instructions above are historical.
