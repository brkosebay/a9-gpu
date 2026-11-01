> Round 7 update: report (`round7-report.md`, not published) and first-render fixture (`first-render.md`, not published)
> identify/encode the actual captured twiddled clear/EOT chains and bounded
> renderpass2 translation. The fixture remains **not submit-ready**: TA/3D,
> HWRT/PM, initial helper state and hardware retirement are missing. Linear
> target capture modes now build; A9 linear support remains untested.

# G5P command buffers, control streams and PDS programs (round 2, 2026-10-22)

Fresh-capture correction: round6-capture-report.md (`round6-capture-report.md`, not published)
recovers actual DDMAD/PPP and24-byte A9 twiddled PBE state. The old render
payload+0x68/+0x80 background/EOT labels below are **unproven**: their USC
at heap+0x180/+0x4c0 is identical in fresh compute and render captures.
Do not use that inference as a store-program ABI. Current numerical kernel
handoff: first-dispatch.md (`first-dispatch.md`, not published).


How the iOS 16.7.10 Metal driver (`AGXMetalA9`) encodes a compute dispatch
and a draw for the kernel/firmware. Evidence:

1. **On-device captures** (`build/gpu-userspace/device/out/`, run 1 of
   device-run.md (`device-run.md`, not published)). These are the driver's GPU-shared pages
   before commit and after completion of one `k_fadd` dispatch (16×64
   threads) and of one `v_novb`+`f_solid` triangle into a 64×64 BGRA8 target.
   device-results.md (`device-results.md`, not published) audits them; the dispatch really
   executed.
2. **Static RE** of `AGXMetalA9`/`IOGPU`, extracted from the iPad's dyld
   cache. The cache's local symbols are applied: `ipsw dyld symaddr --image
   AGXMetalA9` recovers 5,214 names, including the C++ `AGX::` internals. The
   Ghidra project is in `build/gpu-userspace/ghidra/`; decompilations are in
   `build/gpu-userspace/cs/`.

Function addresses are unslid cache VAs: `0x207c…` = AGXMetalA9 and
`0x1ec2…` = IOGPU. CPU addresses are the capture process's. **Verified**
means checked against captured bytes or decompiled code; **inferred** means
interpretation. This round merges two independent analyses (a Claude
sub-agent and a Codex agent). Where they disagreed, the bytes or Mesa
source decided, as noted below.

## Headline

- **Verified:** the control streams and PDS programs use **Imagination's
  Rogue formats as described by Mesa**. CDM (`csbgen/rogue/cdm.xml`) and VDM
  (`vdm.xml`) block types, count−1 conventions, link and terminate words all
  decode, as do the PDS opcodes, with `d3000000` = HALT exactly. Only the USC
  shader ISA differs from Mesa ([isa-g5p.md](isa-g5p.md)).
- **Verified, PDS DOUT layout:** Apple packs DOUT sources at src0 `[9:3]`,
  src1 `[17:10]`, src2 `[24:18]`. This is exactly Mesa `pdsc`'s
  `PDSC_FEATURE_DOUT_EXT` variant (`pdsc_isa_pack.h:pdsc_dout_pack`), and
  the only layout whose operands fall inside each program's data segment.
  The older `vulkan/pds/pvr_rogue_pds_defs.h` describes only the non-EXT
  layout (`[14:8]`/`[23:16]`). So the "packing difference" one analysis
  reported holds only against that legacy header. Mesa's `pdsc` with
  `DOUT_EXT` is a direct starting point.
- **Verified Apple additions:** CDM block type 3 (`c0000003`), not in
  Mesa's enum, and several driver-internal helper tasks around each
  dispatch and draw.
- The records described here go from userspace to Apple's kernel. The
  kernel's translation into firmware commands is separate work; see (f).

## (a) Object model

```
MTLCommandBuffer (AGXA9FamilyCommandBuffer)
 ├─ kernel-command buffer   CPU-written, parsed by the kext: one record per
 │                          encoder (compute 0x1f0 bytes, render 0x820 bytes)
 ├─ segment list            IOGPU shmem header + per-segment resource lists
 ├─ data buffer(s)          DataBufferAllocator: CDM/VDM streams, PDS data,
 │                          per-draw PDS programs, PPP state, arguments
 └─ code heap               device-wide USC + PDS programs of pipelines
submit: IOGPUCommandQueueSubmitCommandBuffers → IOConnectCallMethod(sel 0x1a)
kext:   AGX{CL,TA,3D}CommandDescriptor → AGX{CL,TA,3D}ChannelG5P → META firmware
```

`-[AGXA9FamilyCommandBuffer commitEncoder]` (`0x207cbce64`) runs deferred
encoder finalization, and `AGX::ContextCommon::endCommand` (`0x207cc5cb0`)
publishes the record and segment (verified). Each encoder starts a new
segment (`beginSegment:` gets the record pointer).

### Kernel-command record (verified: code + both captures)

`AGX::ComputeContext::beginComputePass` (`0x207d0c210`) and
`AGX::RenderContext::beginRenderPass` (`0x207d6a30c`) reserve the record with
`growKernelCommandBuffer:` and zero the payload. Header constants come from
`__const` at `0x207ddbc98`/`0x207ddbca0` (compute) and `0x207ddbce8`/`0x207ddbcf0`
(render).

| Offset | Compute (`0x77bdf0000`) | Render (`0x4cbdf0000`) | Meaning |
|---|---|---|---|
| +0x00 | `0x00010000` | `0x00010000` | record tag |
| +0x04 | `0x1f0` | `0x820` | total size |
| +0x08 | `4` | `4` | flags; 4 = not owned by a parallel encoder (code) |
| +0x0c | `0` | `0` | skip flag |
| +0x28 | `0` before commit, `0x1e8` after | `0x818` after | `+0x2c + 0x30`, written at commit |
| +0x2c | `0x1b8` | `0x7e8` | payload size |
| +0x30 | `0x30` | `0x30` | common subheader size |
| +0x34 | **3** | **1** | command kind: 3 = compute (CL), 1 = render (TA+3D). Values verified; names inferred |
| +0x38 | payload | payload | see (b), (c) |

### Segment list (verified: IOGPU code + capture `0x77bdec000`)

From `IOGPUMetalCommandBufferStorageBeginSegment` (`0x1ec2a3118`),
`EndSegment` (`0x1ec2a8758`) and `FinalizeShmemHeader` (`0x1ec2a7e20`):

```
+0x00 u64 trace object ID
+0x08 u32 segment count = 1
+0x0c u32 list size | 0x80000000   (bit 31 set by FinalizeShmemHeader)
segment[0] @+0x10:
  +0x00 u64 globalTraceObjectID
  +0x08 u32 start offset in kernel-command buffer = 0
  +0x0c u32 length = 0x1f0
  +0x18 u32 (storage+0x2ac) = 0xd,  +0x1c u32 resource count = 3
  +0x20 IOGPUResourceList entries, 0x40 bytes each
```

Submit is `IOConnectCallMethod(conn, 0x1a, scalars{queue_id, n, count,
stride}, 4, array, count*stride)` in `IOGPUCommandQueueSubmitCommandBuffers`
(`0x1ec2a5118`) (verified).

## (b) Compute dispatch

### Compute payload C = record + 0x38 (0x1b8 bytes)

| C offset | Run-1 value | Meaning |
|---|---|---|
| +0x00 | `0x10005c000` | **CDM stream GPU VA** (`DataBufferAllocator::getBaseGPUAddress`) (verified) |
| +0x08 | `0x100044000` | context-switch scratch base (0x6068-byte allocation) |
| +0x10/+0x18/+0x20 | base +4/+8/+0xc | scratch field pointers |
| +0x28…+0x50 | `0x0003000000000400`, `0x50800005`, `0x0000043000000420`, `0x40200003`, `0x0003002000000740`, `0xa00004` | packed PDS states of two context-switch programs |
| +0x58 | `0x10004a020` | scratch near end (inferred) |
| +0x60 | `0x10005c0c4` after commit | **GPU VA of the CDM terminate word** (not one past it), written by `endComputePass` (`0x207d0bfa0`) |
| +0xa8 | `0x070040d9` after commit | bookkeeping (same value as the segment trace ID) |

`G5::ContextSwitcher::setupComputeCommand` (`0x207cc6250`) builds
+0x08…+0x58 and emits two patchable PDS data segments for context-switch
programs (verified). **Inferred:** this is Apple's preemption mechanism.
Which parts the firmware needs for one non-preemptible job is open.

### CDM control stream (verified, 7-word records)

GPU `0x10005c000` = CPU `0x77be38000` in the run-1 post capture.
`performEnqueueKernel` (`0x207d0b218`) appends 28-byte records taken from
the program variant (`variant+0x9b8…`). `ComputePDSStateLoader::
emitComputeProgramVariantAndArguments` (`0x207cc0438`) patches the
common-store size into word 0 and the counts in. `endComputePass` appends
the terminate word.

| Stream offset | W0 | W1 data | W2 code | W3/4/5 (count−1) | W6 | Role |
|---|---|---|---|---|---|---|
| +0x00 | `00040004` | `00030042` | `0x2400` | 0,0,0 | `40000000` | argument/constant loader for k_fadd (`doutD` ×2) |
| +0x1c | `00042103` | `0x2500` | `0x2480` | **15,0,0** | **`00fc0000`** | **k_fadd: 16 groups of 64** |
| +0x38 | `00040004` | `0x143ca` | `0x14340` | 0,0,0 | `40000000` | driver task: DMA 4 dwords from `0x100000100`, USC `0x14280` |
| +0x54 | `00000002` | `0x1418a` | `0x14100` | 0,0,0 | `40000000` | driver task (conditional `doutU`) |
| +0x70 | `c0000003` | `0x14214` | `0x14200` | 0,0,0 | `40000000` | **Apple type 3**, driver program at `0x14200` (inferred: fence/flush) |
| +0x8c | `00000003` | `0x14454` | `0x14440` | 0,0,0 | `40000000` | driver task (conditional `doutU`) |
| +0xa8 | `c0000003` | `0x14214` | `0x14200` | 0,0,0 | `40000000` | type 3 again |
| +0xc4 | `80000000` | | | | | STREAM_TERMINATE (appended at commit; absent before) |

- **W6 (verified from the emitter):** `(maxInstances << 27) |
  ((threads.x−1) << 18) | ((threads.y−1) << 9) | (threads.z−1)`.
  `0x00fc0000` = 64×1×1 threads with maxInstances 32 encoded as 0;
  `0x40000000` = maxInstances 8, 1×1×1.
- **W0 under Mesa's KERNEL0 layout (inferred):** common store [26:18] in
  64 B units, unified [17:12] in 16 B, PDS temps [11:8], PDS data [7:2],
  target-any bit 1, fence bit 0. **Round-4 inferred refinement:** PDS data
  is `ceil(bytes/16)-1`: k_fadd's 16-byte segment gives 0 and the loader's
  recovered 32-byte segment gives 1. Two measured allocations support
  this rule; other sizes and `one`/`store` still need validation.
- **Links (verified):** `agxaReserveCDMTokenSpace` (`0x207cba56c`) emits
  `[0x40000000 | gpu>>32, gpu & ~3]`, the Rogue CDM link.

### Compute PDS programs (verified decode, DOUT_EXT layout)

`tools/gpu-userspace/pds_dis.py` and `g5p_cs.py pds` disassemble them:

```
k_fadd   code @0x2480: c2c00003 f0000a01 d3000000 f0000e01 fc000002 d3000000
                       bra !p -> +3 ; doutW t0:1,c2 ; halt ; doutW t0:1,c3 ; doutU.end c0:1 ; halt
         data @0x2500: 000022c0 00000010 90000000 80000000
                       c0:1 = USC exe_off 0x22c0, temps/4 [40:35] = 2 (8 temps); c2/c3 = DOUTW control
loader   code @0x2400: f0001000 f4001808 d3000000      (doutD c0:1,c4 ; doutD.end c2:3,c6 ; halt)
```

- **The chain archive → resident USC → PDS → CDM is closed (verified).**
  The device-compiled 194-byte k_fadd USC sits at CPU `0x77bdba2c0` (code
  heap offset `0x22c0`, as DOUTU `exe_off`). CDM +0x1c points at PDS code
  `0x2480` / data `0x2500`.
- Emitters (verified, `PDSEncoderGen1::EncodeComputePDSProgram`
  `0x207d1ea4c`):
  - `0xc2c00000 | delta` is a branch over the first path.
  - `0xfc000002 | (cidx>>1)<<3` is DOUTU; its data word is the USC address
    `& ~3` plus a high word from the launch template.
  - `0xf0000201 | (cidx & 255) << 10` is DOUTW.
  - Optional `0xf0000005` is DOUTC (exact use unknown).
- **DOUTU src0 (verified, Mesa `pdsc_doutu_src0`):** exe_off [31:0],
  temps/4 [40:35], sample_rate [34:33].
- **Argument loading (verified):**
  - `EncodeShaderConstantsPDSProgram` (`0x207d1e010`) and
    `agxaEmitPDSDataSegment` (`0x207cb8ecc`) build the DMA data from
    relocations `argumentTable[i] + addend`.
  - The table at CPU `0x77be2e068` starts with the three buffer GPU VAs
    `0x100000200/1200/2200`.
  - **Verified round-4 correction:** the 32-byte loader data at heap
    `0x30040`, CPU `0x77be20040`, is present in **both** pre/post dumps.
    Strict reads recover the following words (the earlier missing-page
    claim was incorrect):

    ```
    0004a068 00000001 0004a088 00000001
    10000006 00000000 9000c003 00000000
    ```

  - **Verified fields:** DOUTD sources `0x10004a068`/`0x10004a088`,
    counts 6/3 dwords, common-register destinations 0/6, last bit only
    on the second DMA. **Inferred mapping:** these sources point into
    the buffer/constant argument table above.

## (c) Render pass + draw

### Render payload (record + 0x38, 0x7e8 bytes; capture `0x4cbdf0000`)

| Record offset | Run-1 value | Meaning |
|---|---|---|
| 0x38 | `0x100040000` | **VDM stream GPU VA** = CPU `0x4cbe1c000` (verified via links) |
| 0x40 | `0x100` | unknown |
| 0x48–0x60 | `0x1001c4020/24/28/2c` | four stamp/scratch slots (as compute) |
| 0x68 | `24001002 00030005 00000380 0 00004820 00000390` | PDS state, data `0x30000`/code `0x380`. Context helper; NOT background (round8) |
| 0x80 | `21001002 00030021 000006c0 0 00004820 000006d0` | PDS state, code `0x6c0`. Context helper; NOT EOT (round8) |
| 0xa8 / 0xc0 | `20000001 00000395 000003a0` / `20000001 000006d1 000006e0` | PDS states for the two above |
| 0x188 | `24000081 00030065 00030070` | PDS state with code in the data buffer |
| 0x2d8 / 0x2e0 | `0x100134000`, `0x100070000` | allocator pools8/7, sizes80000/c0000, measured full pre-submit zeros (round12); NOT PM/HWRT |
| 0x308 / 0x310 | `0`, `0` | framebuffer depth copies from+9a0/+9a8; round12 corrects the earlier payload/whole-record basis error |
| 0x4d0 | `3f800000 00000300 0000000f` | 1.0f, inferred clear depth |
| 0x690 | `0x40 0x40 1 8 8` | **64×64 render size** (verified value), then 1, 8×8 (inferred: samples, tile grid) |

`beginRenderPass`/`endRenderPassCommon` (`0x207d699cc`) write these.
`Framebuffer::configurePBE` (`0x207d10144`), the scratch image/PBE state
(`0x207d15e78`) and store finalization (`0x207d1667c`) prepare the
render-target side (verified functions; word layouts open).

### VDM control stream (verified layout = Mesa `vdm.xml`)

CPU `0x4cbe1c000` = GPU `0x100040000`; block type is bits [31:29]:

```
80000001 000400dc        STREAM_LINK -> 0x1000400dc (lands on the next block)
...                      two driver draws (index count 1)
44002f80 000026c0 00000004 00001081
                         VDM_STATE0 + STATE3 vs_pds_code = 0x26c0,
                         STATE4 vs_output_size = 4, STATE5
00000401 00026f60        PPP state update: 4 words at 0x100026f60
48002f80 000302c0        VDM_STATE0 + STATE2 vs_pds_data = 0x302c0
00000901 001ca3e8        PPP state update: 9 words at 0x1001ca3e8
6e440600 00000003 00000000 00000000 000000aa
                         INDEX_LIST: topology 6 (triangle list), count 3,
                         instances−1 0, start 0, split count 0xaa (170)
...  6e440900 00000004 … driver 4-vertex strip after the user draw
c0000000                 STREAM_TERMINATE
```

- **Draw emission (verified):** `drawPrimitives:vertexStart:vertexCount:`
  (`0x207d5f354`) emits changed state, then `0x6e440000 |
  primitiveTable[type]`, the count, `instances−1`, the start and
  `splitCountTable[type]`. `PrimitiveTypeToVDMCTRLType` (`0x21076f968`) maps
  Metal's first five types to `0, 0x100, 0x300, 0x600, 0x900`. Its split
  table (`0x21076f990`) gives 170 for triangle lists.
- PPP updates are `[gpuMSB | wordCount<<8, gpuLSB]`, and links are
  `[0x80000000 | gpu>>32, gpu & ~3]` (`agxaReserveVDMTokenSpace`
  `0x207cb6d7c`) (verified).
- **Triangle chain (verified):**
  - `vs_pds_code 0x26c0` = `f0000a01 fc000002 d3000000`: `doutW t0:1,c2`
    passes the vertex index from a PDS temp, then `doutU.end c0:1`.
  - `vs_pds_data 0x302c0` gives `c0:1 = 0x10_00002540`, which is **v_novb's
    USC at heap `0x2540`**.
  - **f_solid:** PDS code at heap `0x2460` is one `doutU.end c0:1`; its
    data at `0x2440` points at USC `0x22c0`.
- **Vertex fetch (verified from the ISA side):** `v_passthrough` (`return
  pos[vid]`) compiles to output writes only, so the buffer read is a
  driver-generated PDS DMA into r0–r3 ([isa-g5p.md](isa-g5p.md)).
- **Fragment/PPP state (partly decoded, inferred):**
  - Fragment-program records `000000c0 00002440 00002340 00800000 …` at CPU
    `0x4cbdde1e0`.
  - 14-word PPP blocks at CPU `0x4cbfcc000` (`0000001f 00240000 103e0f00 …`)
    read as a Mesa `STATE_HEADER` with ISPCTL/ISPA/ISPB front and back
    present (ISPA dcmpmode always).
  - Viewport `42000000 42000000 42000000 c2000000 00000000 3f800000` =
    offset/scale 32/32, 32/−32, 0/1 for 64×64, i.e. Mesa PPP viewport order
    (values verified).
- **Parameter buffer (inferred):** at CPU `0x4cbfd4000`, entries like
  `0x00814eb4` in runs of 4 fit DRAM page numbers. The 2.3 MB region at
  `0x4cbff4000` holds tile/region-header-like arrays.
- **What the kext adds (inferred from class names):**
  - `AGXTACommandDescriptor` (0xd88, subclass of `AGX3DCommandDescriptor`)
    splits the render record into a TA (geometry) and a 3D (fragment)
    firmware command.
  - `AGXParameterManagementMeta` + `AGXRenderTargetMemoryG5` own the
    parameter buffer, free lists and region headers.

## (d) Heaps and GPU-VA conventions

- **One code-heap base B for USC and PDS (verified).** Every CDM/VDM PDS
  code/data address and every DOUTU `exe_off` is an offset from the same
  base:
  - USC: k_fadd and f_solid at `0x22c0`, v_novb at `0x2540`.
  - PDS: `0x2400`–`0x2580` and `0x26c0`.
  - Driver programs: B+0x14000 and B+0x28000.
  - Per-command-buffer data (per-draw PDS data, argument loaders): B+0x30000.

  The kext/firmware must program B into the USC/PDS code-base registers.
  Its VA and register are open (kernel side).
- **Alignment (verified):** CDM/VDM addresses use bits [31:4] (16-byte
  aligned). Code-heap allocations are 64-byte aligned, behind a 64-byte
  header whose first word is the size (inferred). DOUTU `exe_off` is
  4-byte aligned.
- **General VA (verified):** buffers sit at `0x1_0000_0000`+ (k_fadd's at
  `0x100000200…`). Streams, stamps and state live in the same 40-bit space.
  CPU↔GPU mappings differ per region; there is no single global offset.

## (e) Minimum job: see [first-triangle.md](first-triangle.md)

The step-by-step plan (replay → own shaders), each stage's exact
buffers/words, and the per-stage kernel requirements are in
[first-triangle.md](first-triangle.md). Still open: the firmware command
layouts, the code-heap base registers, cache-maintenance policy, the
minimum context-switch state, the type-3 packet semantics, the PBE/ISP/
region-header words, and TA/3D parameter-buffer setup.

## (f) What the kernel agent should recover from the kext (shared Ghidra)

1. **CL path first:** how `AGXCLCommandDescriptor` (vtable
   `0xfffffff006ff2d68`, size 0x438) parses the 0x1f0 compute record (tag
   `0x10000`, kind 3). Then which AGFI command it emits on
   `AGXCLChannelG5P`: the CDM base and terminate fields, the scratch
   pointers, and the code-heap base B.
2. **TA/3D path:** `AGXTACommandDescriptor`/`AGX3DCommandDescriptor` (vtables
   `0xfffffff006ff98c8`/`…9ab0`), `AGXParameterManagementMeta` (free list/PM)
   and `AGXRenderTargetMemoryG5`.
3. **Where B is allocated and mapped**, and how it reaches the firmware.
4. **User-client selectors:** `0x1a` (submit) and resource creation
   (`IOGPUResourceCreate` → `IOConnectCallMethod`), to map onto a Linux
   BO/submit UAPI.

## Captures that would close gaps

- `regions one`: the minimal 1×1 dispatch, the first replay target.
- `regions store`: 64-thread `k_store_const`, the simplest dispatch with a
  thread index.
- **Inferred later priority:** `regions vc` / `tex`: a vertex-buffer triangle and a textured triangle.
  Their buffers/texture resource IDs are logged, to tie VDM/PPP/PBE words
  to real objects.
- **Inferred later priority:** differential render captures (3 vs 6
  vertices, two clear colors, BGRA8 vs RGBA8) to isolate clear and PBE
  words. No page-touching capture is required for the loader data: it is
  already present. Keep strict sparse reads for other ranges. The minimal
  next window is device-run.md (`device-run.md`, not published).

## Round 4 external command emitter and mock

**Verified offline:** `g5p_compute_pack.c` uses actual Mesa `pdsc` and
`csbgen/rogue/cdm.h` to generate the measured programs/fields.
`g5p_compute.py` independently packs the same bytes: six groups agree with
Mesa, and eight strict A9 capture ranges agree byte-for-byte (loader and
launch PDS, launch/loader data, two CDM kernels, terminate and a type-3
helper packet). No A9 `one`/`store` capture exists yet; host M4 captures
do not validate G5P dispatches.

**Inferred minimal job:** a one-DMA loader of the output pointer, a
one-thread USC launch and terminate, with USC/PDS/data mapped relative to
a synthetic common base. This omits unproven helper tasks and does not
claim valid firmware/context state. The 16-byte one-DMA data allocation
uses the inferred size field 0. The type-3 capture can be reproduced as
bytes, but the executor rejects executing it rather than ignoring it.

**Verified mock:** `compute-harness.py` walks the generated CDM/PDS/DMA/USC
bytes, checks mapped resources, reports C1–C3 results and tests fourteen
negative jobs. Its kernel path is blocked, with a future transport adapter
seam. See round4-report.md (`round4-report.md`, not published) for measured compiler/mock
coverage and uapi-needs.md (`uapi-needs.md`, not published) for the reviewed kernel contract.

## Tools and reproduction (offline)

- `tools/gpu-userspace/regions.py DIR STAGE find|find32|find64|dump|diff|pages`:
  a sparse address space over the dumps. It zero-fills absent pages, so a
  zero there isn't proof.
- `tools/gpu-userspace/g5p_cs.py regions|cdm|pds|run1`: locates
  kernel-facing records and draws, and decodes CDM (stops at type 3) and
  PDS. Strict reads: uncaptured pages are rejected.
- `tools/gpu-userspace/pds_dis.py`: Rogue PDS disassembler that prints both
  DOUT layouts.
- `sh tools/gpu-userspace/command-stream.sh [--decompile]`: extracts the
  run-1 records/streams into `build/gpu-userspace/cs/round2/`, and
  optionally decompiles the 30 functions cited here (Ghidra 12.1.4
  headless).
- `tools/gpu-userspace/ghidra/ApplySyms.java`, `DecompileList.java`: apply
  the dyld-cache local symbols and batch-decompile.

Generated outputs (gitignored) are under `build/gpu-userspace/cs/`. No
device commands, commits or `docs/gpu/` edits were made by this analysis.
