# G5P work submission — round 5, offline 20H350 analysis

**Scope:** verified host-side writes/layouts below come from the analyzed
j71t AGXG5P kernelcache. Semantic names inferred from those operations are
identified. No round-4 or round-5 GPU code has run on device; integ10 stopped
in audio before GPU testing. The separate GPU-userspace workstream's inputs
are [command-stream.md](../gpu-userspace/command-stream.md#f-what-the-kernel-agent-should-recover-from-the-kext-shared-ghidra)
and [first-triangle.md](../gpu-userspace/first-triangle.md). Their files were
read, not edited. Apple bytes and full decompilations remain under build.

## CL payload → work object → microsequence

**Verified:** the compute record is 0x1f0 bytes, payload C starts at +0x38
and has 0x1b8 bytes (userspace evidence). Import method
`0xfffffff006c8b280` copies C fields into an `AGXCLCommandDescriptor`
D (vtable `0xfffffff006ff2d68`, object size 0x438).
`0xfffffff006c8afcc` resolves the GART context and acquires firmware work /
dependency objects. `AGXCLChannelG5P` vtable `0xfffffff006ff73c8` routes
`+0x88` to builder `0xfffffff006c8a98c`. That builder writes firmware work
W, calls hardware-field fill `+0x148` → `0xfffffff006ca90cc`, microsequence
emit `+0x140` → `0xfffffff006ca8da4`, then pointer-ring publish `+0xc0`
→ `0xfffffff006c88840`. W starts with u32 type **3**, u32 zero (constant
at `0xfffffff005940b40`); this is distinct from the scheduler's type 2.

**Verified selected field chain:** all are byte offsets. C comes from the
GPU-userspace record; W pointers are GPU virtual addresses unless the host
explicitly converts them to 32-bit META addresses.

| C | D | W | Width / meaning |
|---:|---:|---:|---|
| 0x00 | 0x2e8 | 0x70 | u64 CDM stream VA |
| 0x08 | 0x2f0 | 0x1ec | u64 context-switch scratch base |
| 0x10 | 0x2f8 | 0x1f4 | u64 scratch field pointer |
| 0x18 | 0x300 | 0x1fc | u64 scratch field pointer |
| 0x20 | 0x308 | 0x204 | u64 scratch field pointer |
| 0x28 | 0x310 | 0x21c | u64 packed PDS context state |
| 0x30 | 0x318 | 0x224 | low u32 context state |
| 0x38 | 0x320 | 0x68 | u64 packed context state |
| 0x40 | 0x328 | 0x10 | low u32 context state |
| 0x48 | 0x330 | 0x22c | u64 context state |
| 0x50 | 0x338 | 0x234 | low u32 context state |
| 0x58 | 0x340 | 0x60 | u64 scratch pointer, **not CDM terminate** |
| 0x60 | 0x348 | 0x23c | u64 address of CDM terminate word |

The copy chains are verified by the three builders; scratch/PDS semantic
names follow userspace's evidence. W also includes context/catalogue index
at +8, converted dependency pointer at +0xc, microsequence META address at
+0x1e0 and its rounded length at +0x1e4, additional stamp/counter addresses
at +0x258/+0x25c and scheduling metadata. These fields cannot be omitted
merely because the shader is tiny. The complete minimal CDM dispatch object
is **not yet validated**.

**Verified microsequence, `0xfffffff006ca8da4`:** optional instrumentation
commands, then opcode 0x32 (13 words), `0x80000018` (5 words), SKU helper
`0xfffffff006ca9218` emitting 0x101 plus the constant at
`0xfffffff005940cc0` and word 1, then 0x18 (5 words), then opcode 0x33
(11 words). `0xfffffff006cb68dc` appends `0x40000017`, rounds length to
64 bytes and returns the META address. The 0x32 arguments reference
W+0x10, W+0x60, global CL state+0x720, the channel state and W+0x1e8;
0x33 includes stamp/counter and a relative backlink. **Inferred semantics:**
start/finish compute, wait for idle/interrupt, and microsequence termination.
These are firmware instruction words, not CDM or USC ISA words. In
particular, writing a userspace CDM stream directly into a 20-byte scheduler
slot is not a submission.

## Three rings, three different entry formats

**Verified host layout:**

| Layer | Host fields / function | Entry / indices |
|---|---|---|
| Per-channel work pointers | channel +0x60 control / +0x68 pointer array; `0xfffffff006c88840` | u32 META pointer; consumer ctl+0, producer +0x40, slot count +0x50; one reserved slot |
| Global CL work notifications | firmware +0x340 control / +0x348 data; `0xfffffff006c980a0` | 256 × 20 bytes; consumer +0, producer +0x10; words `{2, channel_state_META, sequence, unchanged, unchanged}` |
| Device-control commands | firmware +0x360/+0x368; `0xfffffff006c98234` | 256 × 20 bytes; separate control protocol, **not** the CL ring |

`0xfffffff006c89420` initializes a channel-state record: u32 +0 control
META address, +4 pointer-array META address, +8 context META address,
+0xc=4, +0x14=channel identity, +0x18=0xffffffff; remaining initial state is
zero through +0x63. There is no invented 64-bit pointer in those fields.
The host writes the command, performs `dmb ish`, publishes channel
producer, then publishes the global notification producer with another
barrier. The diagnostic uses coherent DMA memory and DMA barriers.

**Verified CL kick:** wrapper `0xfffffff006ca0358` waits for firmware alive,
calls `...6c980a0`, handles the Fender state, then writes **R0+0xb00=0x12**.
TA wrapper `0xfffffff006ca00a0` uses 0x10. Device-control wrapper
`0xfffffff006ca05d4` also uses 0x10. The generic kick documented in earlier
rounds must not be substituted for the CL-specific value. `0xfffffff006c97f10`
is the 3D notification path (type 1), while TA `...6c97d84` emits type 0.

## Dependencies and completion

**Verified:** `0xfffffff006c8a808` builds 16-byte dependency records:
u32 tag 4; u32 converted stamp address; u32 expected stamp value;
u32 sequence masked with 0x7fffffff. It publishes these through the same
per-channel pointer ring before the compute object. **Inferred:** an
already-satisfied dependency is a firmware-only NOP useful for testing the
queue transport before executing CDM work.

**Verified:** event drain `0xfffffff006c99788` handles 44-byte events. Type
1 carries four u32 bitmaps at +4/+8/+0xc/+0x10, representing up to 128 stamp
notifications; it calls the stamp manager for each bit and schedules
completion processing. It is not a `{job_id,status}` event. Type 0 is boot
alive; type 4 enters recovery, type 7 queues a page-request/fault path,
and type 8 handles channel error. `0xfffffff006c8ad04` walks completed
channel indices; `...6c8ade4` notifies the stamp machinery / allocation
retirement. Future DRM fences must be tied to the actual stamp value and
retirement contract. Merely observing global work-ring consumption is
insufficient to signal a real compute fence.

**Implemented experimental first work:** `work-prepare`, `work-submit`,
`work-wait`, gated by both boot/alive state and separate default-false
`work_experiment`. It uses a private 1 KiB area at shared offset 0x54000
(disjoint from the recovered 0x50f00 used span), a 16-slot pointer ring,
and one tag-4 dependency expecting 1 at a private stamp prefilled with 1.
The context pointer and channel identity are zero. These zero choices and
the dependency-as-NOP composition are **inferred**, not a captured valid
Apple channel. Submission requires an empty CL work ring and active Fender;
it does not attempt Apple's unimplemented power-restart path. It publishes
one type-2 notification and kick 0x12. `work-wait` requires both consumers
to advance, producer/count/stamp integrity and no observed fault event,
within three seconds. It preserves memory on failure. `queues` exposes the
raw CPU/DMA state without MMIO or a blocking mutex. This proves queue
progress if successful; **it does not prove a GPU shader ran**. See
experiment 04 (`experiments/04-cl-queue.md`, not published).

## Code-heap base B: recovered facts and remaining boundary

**Verified host facts:** GART getter `0xfffffff006caf7dc` returns base
`0x300000000`, length `0x100000000`. The range table at
`0xfffffff0059426d8` starts with that same range. Common META init writer
`0xfffffff006c96be8` sets two u64 fields at META init +0x10 and +0x18 to
`0x300000000`. Our boot builder already reproduces those fields. The
range allocator maps physical BO pages into the GART; B is a GPU VA,
not a physical contiguous allocation or an offset into firmware memory.

**Inferred:** these are Apple's common USC/PDS execution bases and
B=0x300000000 is the leading candidate. Upstream separately supplies
`pds_exec_base`/`usc_exec_base` through `fw_sysinit_init` in `pvr_fw.c`, but
its heap constants are different and its FWIF layout does not prove the
Apple field identities. Userspace's relative-offset evidence agrees with
a common base, but does not establish its absolute address. **Unresolved:**
the Apple firmware's reads of those init fields and final USC/PDS MMIO
writes; a per-context alternative base has not been ruled out. No guessed
code-base register write or captured code relocation is implemented.
For C1 replay, verify B before mapping the captured code at B+offset.
This is the specific remaining code-heap question for the userspace agent;
this note is the coordination artifact, without changing its files.

## TA/3D and parameter buffer, lower-priority findings

**Verified:** TA descriptor import `0xfffffff006cba408`, 3D descriptor
import `0xfffffff006c7b100`, TA builder `0xfffffff006cb9050`, and 3D builder
`0xfffffff006c7a06c` are distinct from CL. TA microsequence
`0xfffffff006ca9368` emits start/finish opcodes 0x23/0x24; 3D
`0xfffffff006ca7388` emits 0x25/0x26; its alternate path
`0xfffffff006ca77a4` emits 0x27/0x28. These share the microsequence
allocator/terminator but have different work-object sizes and pointers.
TA/3D cannot be obtained by changing the CL ring's type word alone.

**Verified:** `AGXParameterManagementMeta` backing allocator
`0xfffffff006cb2378` computes total size from per-core strides, maps the
allocation, and fills each per-core PM object's +0xa0, +0xa8, +0xb0 GPU
pointers and +0xb8 META pointer. Its +0x168 implementation
`0xfffffff006cb2244` is a prerequisite for that allocation. These refer to
page lists/control state, not just a single parameter-buffer address.
`AGXRenderTargetMemoryG5` checks dimensions <=0x4000 at
`0xfffffff006ca6d38`; its family-specific getters start at
`...6ca6d24`. **Unresolved:** complete freelist initialization, firmware
ownership/resize protocol, TA-to-3D dependency stamps, region headers and
PBE/ISP state. No render submission is implemented this round.

**Evidence limitations:** stripped virtual methods missing in the shared
function database were decoded in the existing auxiliary Ghidra project;
shared code-address comments were added for all understood entry points,
and functions present in the shared database were renamed. Auxiliary names
are reproduced by `tools/gpu/ghidra_offline.py`. Static analysis establishes
host stores and control flow, not that the new Linux composition executes.
