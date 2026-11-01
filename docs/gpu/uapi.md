# Apple G5P DRM UAPI — round 10 working contract

**Round20 supersedes the compute/live-VM portions below:**
bounded user-BO implementation (`experiments/20-user-compute.md`, not published) adopts the
round14 bit15/command0f contract on `a9/gpu 87b7d90e7664`. The
published header (`apple_g5p_drm.h`, not published) now matches that head. Its default-off
`compute_user_experiment` gate enables the implemented first-store dialect,
fixed execution geometry and user-VM attachment; hardware qualification is
still pending. Existing160-byte SUBMIT and the fixed type3 fixture remain
separate. The round10 description below records the earlier interface.

**Implemented offline; experimental ABI v1.** The separate render
driver name is `apple_g5p`, not `powervr`. It targets a G5P winsys behind
Mesa pvr's abstraction, not binary `pvr_drm.h` compatibility. Source of
truth is `include/uapi/drm/apple_g5p_drm.h` on `a9/gpu`.

**Coordination:** the userspace review (`../gpu-userspace/uapi-needs.md`, not published)
accepts the separate winsys and capability boundary. Round 7 adds explicit
unknown execution geometry, a live-context capability (clear), and a stable
per-file job result. Full compute state, live attachment and cache retirement
remain unresolved; this is not final userspace sign-off.

The published header (`apple_g5p_drm.h`, not published) is an exact source export from
`include/uapi/drm/apple_g5p_drm.h` at the round-10 kernel head. Existing v1
layouts/numbers are unchanged; additive queries use 0x0b/0x0c/0x0d.

**Verified implemented interface, device untested:**

- `GET_INFO`: ABI version, GPU page shift 14, 40-bit VA interval, firmware
  reservation, maximum BO size, static target BVNC and capability bits.
  `code_base=0` unless the common USC/PDS base is verified. The candidate
  `0x300000000` is research evidence, not a userspace relocation contract.
- `CREATE_BO`, `BO_MMAP_OFFSET`, standard GEM close: shmem GEM objects,
  CPU mmap, READ/WRITE/EXEC role flags, 16 KiB alignment, maximum 64 MiB.
  `BO_SYNC` performs whole-BO streaming DMA maintenance in the selected
  direction; a completed GPU fence must additionally cover GPU-side caches
  before real compute capability can be enabled.
- `CREATE_VM`, `VM_BIND` map/unmap, `DESTROY_VM`: per-file VM handles,
  caller-chosen VAs, BO subranges, overlap rejection and actual three-level
  16 KiB GART preparation. Firmware global range is reserved. VM tables
  are not attached to firmware contexts until context-index/TLB/cache
  programming is established; `VM_PREPARE` and `VM_LIVE` are distinct caps.
- `CREATE_CONTEXT`/`DESTROY_CONTEXT`: compute context handles tied to a VM.
  This initially records host ownership; it does not claim a firmware
  context exists. VM destruction is rejected while contexts exist. After
  an accepted dependency probe, that fd's context destruction, VM mutation
  and VM destruction return EBUSY until close; the device-owned workspace
  is preserved until reboot. A queued job now holds its context, which holds
  its VM and thereby the VM's GEM mappings until software retirement. These
  tables are host-only; firmware still accesses only device-owned memory.
- `SUBMIT`: typed job, context, CDM/terminate VAs, BO handles, scratch
  fields and arrays of binary/timeline syncobj points. Unknown flags and
  reserved values are rejected. One signal is required; inputs are waited
  with a shared deadline. Accepted work gets a monotonic job ID and an
  asynchronous dma_fence in the output syncobj.
- `GET_STATUS`: cached alive/failure, job/error, IRQ/event counters, four
  stamp-notification bitmaps and the last complete 44-byte event. No MMIO.

**Initial supported submission:** one privileged, explicitly gated
`CL_DEPENDENCY` probe per boot. It uses the recovered channel pointer ring,
type-2 CL notification and kick 0x12. Its fence means the pre-satisfied
dependency was consumed with intact queues/stamp and no observed fault;
it does not mean a shader wrote a BO. `COMPUTE`, `RENDER`, `VM_LIVE`,
`CODE_BASE_VERIFIED` and PRIME capabilities remain clear. Compute submit
returns EOPNOTSUPP before queue writes until its missing firmware structures
are implemented. This boundary is deliberate and queryable for Mesa.

## v1 details for a winsys implementation

**Verified source contract:** all structs use fixed-width fields and u64
user pointers; no native pointer or unsigned-long UAPI fields. Ioctls use
the DRM command base plus the following numbers. Unknown flags/reserved
values are rejected. Handles are per drm_file; GEM close is the core ioctl.

| Command offset | Struct / bytes | Essential inputs and outputs |
|---:|---|---|
| 0x00 | `drm_g5p_get_info` / 104 | `size=104`, flags/reserved=0; returns ABI=1, page shift=14, capabilities, VA/reservation/BO limit, static target BVNC. Output fields may be zero on input. |
| 0x01 | `drm_g5p_create_bo` / 16 | Aligned nonzero size <=64 MiB; nonzero READ/WRITE/EXEC role mask, handle=0. Returns GEM handle. Roles are metadata; hardware permission encoding is not advertised. |
| 0x02 | `drm_g5p_bo_mmap_offset` / 16 | GEM handle, flags=0, offset=0. Returns fake mmap offset; mmap through render fd. |
| 0x03 | `drm_g5p_bo_sync` / 24 | GEM handle, direction 0=device or 1=CPU, aligned nonempty BO subrange. Performs whole-BO DMA sync, which is stronger than subrange. |
| 0x04 / 0x05 | create/destroy VM / 8 each | Flags=0; create handle=0, destroy an existing VM without contexts. |
| 0x06 | `drm_g5p_vm_bind` / 40 | VM/BO, fixed VA, size, BO offset, op=0 MAP or 1 UNMAP, flags=0. UNMAP requires bo=offset=0 and an exact complete prior mapping. |
| 0x07 / 0x08 | create context / 16, destroy / 8 | Compute type=0, VM, flags=0; returns context handle. Host-only ownership until compute capability exists. |
| 0x09 | `drm_g5p_submit` / 160 | Context, type, flags=0, sync-array pointer/count, job_id=0. Dependency type=0 requires CDM/terminate/BO list/scratch/pad all zero; returns job_id=1 for the only accepted probe. Compute type=1 currently EOPNOTSUPP. |
| 0x0a | `drm_g5p_get_status` / 96 | flags/reserved=0; returns cached status and full last event, no MMIO. |
| 0x0b | `drm_g5p_get_exec_info` / 80 | size=80, flags/pad/reserved=0; returns scope NONE and zero bases/window/alignment/offset widths until verified. |
| 0x0c | `drm_g5p_get_job_status` / 32 | Owned job_id, flags/pad/reset_generation=0; returns PENDING/-EINPROGRESS or COMPLETE/error. Unknown or another fd's ID: ENOENT. No MMIO. |
| 0x0d | `drm_g5p_get_context_status` / 32 | Owned context, every other field zero on input; returns HOST_ONLY=0 or LOST=1, VM map count and mapped bytes. Unknown context: ENOENT. Cached RAM only; capability CONTEXT_STATUS (bit11). |

VA interval is `[0x10000, 1<<40)`; reserved firmware span is
`[0xe1c0000000, 0xe1c4210000)`. BO size/offset/VA/map size align to 0x4000.
MAP keeps a GEM reference after handle close. Overlap returns ENOSPC from
drm_mm; absent handles return ENOENT; invalid range/flags return EINVAL.
Translation/alignment outside the physical 40-bit identity DMA contract
returns ERANGE. Per-file limits: eight VMs, 32 contexts; per-VM 256 maps,
256 MiB mapped and 512 coherent table pages including root. Empty tables
are reclaimed on UNMAP and failed-MAP rollback. Tables are not installed
in a live hardware context and no TLB invalidation is implied.

`drm_g5p_sync` is 16 bytes: `{u32 handle, u32 flags, u64 point}`.
Point 0 is binary; nonzero is a timeline point. Flag SIGNAL=1 selects the
one required output; flags 0 denotes an input. Maximum 17 entries means
at most 16 inputs and exactly one signal. Inputs must already have a
submitted fence; no WAIT_FOR_SUBMIT. Unsubmitted inputs fail the ioctl
without replacing the output. Existing retained payload errors propagate;
core-retired points represented by signaled stubs cannot recover old errors.
A timeline output rejects a preceding non-timeline payload and must increase;
a binary output replaces the current payload. Concurrent core syncobj mutation is the caller's
responsibility. There is no implicit BO reservation-fence integration yet,
because the only supported job references no user BO.

Queueing plus input waiting has a common one-second deadline; expired jobs
signal an error without a later hardware kick. The hardware wait is three
seconds, and the core command mutex is trylocked. Accepted jobs execute on
a workqueue and outlive fd close; submission itself performs no MMIO.
`GET_STATUS.last_error` is EINPROGRESS while pending, then 0 or a negative
errno, published before fence signal. A terminal firmware fault remains
an error even if a queue-consumer poll concurrently passed. Ordinary
syncobj wait reports completion, so also inspect status or exported fence
status to distinguish success from an error signal. No reset/retry or
recovery is implemented. MMIO can still hard-lock hardware despite bounds.

**Coherency boundary:** BOs use shmem pages, DMA-map SG, and CPU mappings
chosen according to the DMA device's coherent property. VM tables and
firmware use separate coherent 16 KiB pages. The GPU SLC/cache completion
contract for user BOs remains unvalidated; the NOP never accesses user BOs.
Therefore no end-to-end GPU write-visibility guarantee, PRIME or actual
compute capability is advertised by this initial frontend.

**Production work still required:** actual context/catalogue attachment,
validated compute microsequence/stamp retirement, code-base programming,
GPU cache maintenance, scheduling/recovery, dma-buf and TA/3D objects.
See [submission-g5p.md](submission-g5p.md) and [drm-design.md](drm-design.md).


## Round 7: userspace review resolutions and remaining compute gate

**Binary ABI:** zero-initialize all request structs; use the exact sizes
and ioctl encodings in the published header. `GET_INFO.size=104` and
`GET_EXEC_INFO.size=80` are exact size checks, not a negotiation allowing
arbitrary tails. New incompatible layouts require a new version/command;
new additive commands can retain v1. All offsets are explicit multiples
of four/eight and pointers are u64, including 32-bit compat callers, which
must zero-extend their pointers. `bo_handles` is u32[], `syncs` is 16-byte
records. No user BO array is dereferenced today: dependency requires its
pointer/count zero, and compute returns EOPNOTSUPP first. Syncs are copied
once, at most 17 records (272 bytes), before acceptance. Bad user pointers
return EFAULT, allocation failure ENOMEM. Kernel/host tests and freestanding ARM32/ARM64 compile-time
layout tests cover the published sizes; a real 32-bit ioctl run is untested.

**Capabilities:** BO_CPU, VM_PREPARE, SYNCOBJ and JOB_STATUS are present.
CL_DEPENDENCY advertises the separately enabled diagnostic type, not
readiness: missing privilege/boot/work gates return EPERM, no alive state
EAGAIN, failed boot EIO, consumed one-shot EBUSY/EALREADY. All non-dependency
job types, including compute/render, return EOPNOTSUPP before queue writes.
CODE_BASE_VERIFIED, VM_LIVE, CONTEXT_LIVE, COMPUTE, RENDER and PRIME stay clear.
CREATE_CONTEXT is host bookkeeping; it succeeds without CONTEXT_LIVE.
Preparing a VM does not imply attach or executable permissions. PRIME
import/export returns EOPNOTSUPP.

**Execution geometry:** GET_EXEC_INFO describes USC and PDS bases separately,
a common usable `window_size`, `allocation_alignment`, offset bit widths,
and `base_scope` NONE/device/VM/context. This kernel returns **all zero and
NONE**. Zero means unknown/unusable, never base address zero or unlimited
window. A future implementation may report equal USC/PDS bases to declare
a common window; if actual windows differ it must extend the query rather
than compress them into a false common length. Ordinary data VAs use the
40-bit bind interval; CDM PDS and DOUTU USC relative address forms need their
own verified geometry before use. The existing firmware hole is the only
currently reserved interval. No separate executable heap is advertised,
and an EXEC BO may be prepared anywhere otherwise valid but cannot execute.
The candidate B=0x300000000/4 GiB is **not** this contract. Static BVNC is
reported independently of `id`'s measured identity; GET_INFO never reads HW.

**BO ownership:** READ/WRITE/EXEC describe intended GPU access. They do not
restrict CPU writes: executable allocations may contain PDS data/constants,
and pure constants require READ without EXEC. Roles are immutable after
create; v1 does not enforce a validated hardware permission encoding.
Successful MAP keeps a GEM reference and resident DMA-mapped backing until
exact whole-map UNMAP or VM/file destruction. Closing the GEM handle does
not release a surviving mapping. Partial unbind is unsupported (ENOENT
unless an exact prior mapping exists); changes require unmap plus map.
Every bound VA, length and BO offset is 16 KiB aligned. No user VM is attached
to hardware, so these prepared maps can be freed at fd close. The only
accepted job uses device-owned private memory. Round10 additionally retains
the context and its entire prepared VM/map ownership chain across fd close.
After acceptance, VM mutation/destruction and context destruction on that
fd return EBUSY until close, including on timeout. Future real jobs must
retain that ownership beyond a hardware timeout until proven retirement or
reset and reject in-flight rebinding. Current context/VM references retire
with the dependency job because none of these user tables is attached to
hardware. That distinction remains a COMPUTE launch gate.

**Cache order required for real compute:** finish CPU writes, BO_SYNC
TO_DEVICE, submit, wait for a *successful* per-job retirement including
GPU writeback and instruction/PDS/cache maintenance, BO_SYNC TO_CPU, then
CPU reads. Both BO_SYNC operations currently use DMA_BIDIRECTIONAL on the
entire DMA-mapped SG table. Caller serializes CPU access and BO_SYNC; there
is no implicit fence wait in BO_SYNC, mmap or VM_BIND. No CPU writes/reads
may race a future job accessing those bytes. CPU DMA maintenance cannot
supply missing GPU cache operations. The current dependency touches no
user BO; its success does not establish any of those GPU cache guarantees.

**Complete compute state:** v1's scratch[12] is reserved and must remain
zero for dependency. It is **not** an escape hatch for guessed raw work
objects. C+0x28..0x50 PDS/context fields, catalogue/context index, scratch
field/tail pointers, stamps and the complete microsequence remain required.
Prefer kernel-owned verified context state with queried scratch sizes;
recover meanings before adding typed userspace fields. A future compute
command also needs a bounded CDM range, validated terminate word within
that range and supplied mappings, and all pointed-to code/data bounds.
The existing cdm/terminate fields alone cannot establish this. No missing
field is silently zero-filled in a compute work object: none is built.

**Sync acceptance and error query:** inputs are snapshotted before replacing
the output; duplicates count toward the 16-input bound and are allowed.
All handles are scoped to the fd. Same-job binary wait/signal on the same
handle is now rejected; timeline reuse on that handle requires a nonzero
input point strictly below the output point. One output remains mandatory.
Input waits happen asynchronously, after acceptance, under the shared
one-second queue/dependency deadline; the ioctl never blocks on GPU MMIO.
Ordinary pre-acceptance validation/allocation/dependency-lookup errors do
not replace the output fence. A late terminal-fault check may consume the
one-shot reservation without publishing a fence; no retry is supported.
As with other output ioctls, a copy-out EFAULT can occur after acceptance:
the supplied output syncobj and GET_JOB_STATUS for ID 1 determine whether
this fd owns the accepted job. They remain inspectable on the same fd.

JOB_STATUS advertises GET_JOB_STATUS. Exactly one probe per device boot
means accepted ID is 1; other fds cannot query it even knowing the ID.
The result is retained until fd close, with no eviction, independently of
later firmware events or GET_STATUS.last_error. Pending state uses
-EINPROGRESS; terminal error is negative errno, zero success. Completion
state/error are atomically published under status_lock before signaling
the output fence. Later faults may change device status but cannot rewrite
that job result. Core-retired timeline stubs may lose historical fence
errors; GET_JOB_STATUS is the authoritative retained result. Per-job status
does not turn a signaled error into successful readback. No per-job fault
VA/stage/reset recovery is decoded; reset_generation remains zero.

**Timeout and park:** an error fence is not proof DMA stopped. Firmware
image/tables/private channel backing stays device-owned until reboot on
success, fault, timeout and fd close. No reset/retry is implemented;
a failed hardware probe latches the boot failed and further work is blocked.
The optional `park` freezes commands and requests Fender core=0/GPC=0;
subsequent sysfs commands return ESHUTDOWN. It never frees DMA, turns off
PMGR, unloads, or reattaches user VMs. GPC=0 is not yet a DMA-quiescence
proof. IRQ handling remains installed to ack pending AIC interrupts.
DMA-buf/modifiers, render/TA/3D and presentation remain later capability gates.

## Round 10: retained contexts and cached context state

The exported header now includes additive `CONTEXT_STATUS` bit11 and
`GET_CONTEXT_STATUS` at command0x0d. Its32-byte fixed layout is
`{u32 context, flags, state, map_count; u64 mapped_bytes, reserved;}`.
Flags and all output/reserved fields must be zero on input. The query
requires an existing per-file context, runs under the file lock, reads no
MMIO and returns no DMA address. HOST_ONLY means a prepared software
context, never a firmware context. LOST means a sampled boot failure or
park latch. Before firmware boot the state remains HOST_ONLY. This is a
cached observation, not an atomic promise that no fault can occur later.
LOST does not change the separately retained job's terminal result.

Job acquisition now takes a context reference under the handle lock;
contexts take VM references; VM mappings retain GEM objects. Rejected
submission unwinds its reference. Fd close drops handle references while
queued work keeps its ownership chain. The worker signals its fence and
drops job references before releasing its DRM-device reference, so final
VM/table/GEM release still has a valid device. Mutation/destruction after
acceptance remains EBUSY on that fd until close, including after completion.
This provides a lifetime foundation for future BO-using work, without
claiming that software completion of a failed GPU job proves DMA quiescence.

No ioctl number or existing layout changed. No firmware context creation,
live catalogue attachment, CDM shader work, VDM/TA/3D submission, GPU cache
retirement or hardware reset/recovery has been verified or enabled.
COMPUTE/RENDER/VM_LIVE/CONTEXT_LIVE remain clear. The current recovery policy
is failure containment plus optional explicit wrapper park and retention
of all firmware DMA memory until reboot. See experiment09 (`experiments/09-dc-matrix.md`, not published).

## Round 12: gated attachment probe and first-fault query

Additive capability bit12 `CONTEXT_ATTACH_PROBE` permits privileged job type2
only with DRM `context_experiment=1` and `boot_experiment=1`. It requires an
empty VM, successful cmd8/IRQ test and a fresh unprepared CL workspace.
The existing 160-byte SUBMIT layout is unchanged: CDM/terminate, BO list and
scratch remain zero; the standard output sync point is mandatory. DC3 slot1
consumption signals the probe's fence. No shader executes. Context status2
`ATTACHED_PROBE` records this diagnostic result; it does not imply a live
compute context. Attached context/VM/root/device references remain quarantined
until reboot, including after fence signal, fault, timeout and fd close.

Bit13 `FAULT_INFO` and query0x0e add the 80-byte `drm_g5p_get_fault_info`:
flags=reserved=0; first terminal event arrival serial, normalized kind,
valid mask, channel-error type/token/stamp and eleven raw words. Kind0 means
none, 1 recovery(event4), 2 page request(event7), 3 channel error(event8),
4 unknown terminal event. Only channel-error fields have a valid bit. Tokens
are firmware tokens, never per-file DRM handles. Page-request address encoding
and recovery reason semantics remain unresolved, so no fault VA is invented.
The first fault survives later notifications and repeated drain attempts.
Queries read cached host state without MMIO and do not consume events or
recover the GPU. Core dmesg identifies the recovered event classes and preserves
all raw fields. No existing ioctl size or execution capability changed.

A returned hardware timeout preserves -110 in job status and the output fence;
a terminal firmware event takes precedence as -5. Explicit wrapper park can
contain a classified attachment timeout, retaining all memory. It cannot
recover a firmware fault or justify BO reuse/readback. See
experiment11 (`experiments/11-context-attach.md`, not published) for the exact run and outcomes.
This is the kernel-side addition; userspace review/copy is still required.

## Round 14: privileged fixed-fixture CDM through the render node

The published header adds `FIXED_CDM_PROBE` at **bit14**, distinct from
`FAULT_INFO` bit13, and job type3. All existing v1 sizes, ioctl numbers and
compute type1 behavior remain unchanged. The capability is advertised only
when `compute_experiment=1`; CAP_SYS_RAWIO, boot_experiment and the existing
alive/cmd8/IRQ prerequisites still apply. Type3 requires an empty owned
compute context and zero CDM/terminate/BO/scratch fields. It dispatches the
kernel's SHA256-allowlisted payload in a separate kernel-owned VM, then
checks the real stamp, numerical store and63 sentinels before signaling its
syncobj dma_fence. Errors signal an error fence and remain in GET_JOB_STATUS.
The user VM stays HOST_ONLY; this is not the bounded user-BO compute payload
requested in uapi-needs.md. General COMPUTE/RENDER/CODE_BASE/VM_LIVE/
CONTEXT_LIVE stay clear, and EXEC_INFO stays unknown.

Only one DRM job is accepted per boot. The optional sysfs repeat experiment
is a separate serial firmware-stamp test, with at most16 jobs reusing the
private context. It requires all channel indices and the global notification
to retire before W/SKU reuse; it does not emit a separate DRM fence for each
sysfs dispatch. All mappings remain retained on success and failure. The
wrapper-cycle experiment retains the runtime-PM reference and requires a
later numerical job to demonstrate that context state survived. It cannot
establish safe PMGR power-off, reset or DMA reclamation.

Use the versioned round14 scripts, not the original round13 runner:
`GPU_COMPUTE_UAPI=1 scripts/gpu/run-compute-host-r14.sh` after integration
and a fresh boot. `tools/gpu/drm_compute.c` builds against this header.
Render submissions and arbitrary userspace compute remain EOPNOTSUPP;
no raw Apple record or META address was added as a public escape hatch.
