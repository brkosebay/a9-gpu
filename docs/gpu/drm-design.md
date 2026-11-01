# From the diagnostic to a Mesa-facing DRM driver

**Round 6 implementation:** the separate default-disabled `apple_g5p`
frontend now shares the exact private bring-up implementation in `core.inc`
with the diagnostic. Each mutually exclusive frontend compiles that source
with private parameter/state names; this is source reuse, not a public
kernel backend ABI. BOs, host-only VM/context preparation, syncobjs and a
one-shot dependency-fence path are implemented and built offline. Actual
compute/render/live-VM capabilities remain absent. Contract and later test:
[uapi.md](uapi.md), experiment 05 (`experiments/05-drm-prepare.md`, not published).

**Proposed design, inferred unless labeled verified.** Keep the diagnostic
until firmware boot and queue/stamp retirement are repeatable. The next
production driver should be a render-only DRM driver; simpledrm continues
to own scanout initially. A render node alone will not accelerate Hyprland:
Mesa also needs a matching winsys/compiler, synchronization and presentation.

## Objects and submission

- **Render node / lifetime:** register `drm_device` with render/GEM features,
  no KMS initially. Per-file objects own VM and context handles. Probe may
  map resources and request IRQs, but runtime PM controls hardware access.
  Release waits for or fails pending fences; it cannot free memory still
  referenced by META. Default-disabled diagnostic parameters are not a
  production lifecycle or recovery policy.
- **BOs:** GEM shmem or DMA-backed pages, CPU mmap, dma-buf import/export,
  explicit roles/permissions for executable code, GPU data and firmware
  objects. Report GPU page size **16 KiB**. Reject partial/misaligned GPU
  mappings and PA/bus translations outside the recovered 40-bit format.
  Pin backing while mapped/in flight. The diagnostic's coherent allocations
  establish a starting policy, not a scalable BO allocator.
- **VM:** `drm_gpuvm` plus `drm_mm` for VA ranges, per-context GART root and
  caller-chosen VA mappings within validated heaps. A 4 KiB host kernel
  would need 16 KiB GPU-page aggregation and cannot reuse today's PAGE_SIZE
  assumption. Preserve firmware global mappings separately from per-file
  user mappings. Use the recovered catalogue/PDE/PTE encodings, permission
  bits and SLC/cache attributes; implement the actual context-index table,
  TLB invalidation and cache flush/invalidate contract before rebinding BOs.
- **Submit:** bounded size/versioned ioctl with VM/context, stream/terminate
  addresses, BO references, explicit scratch resources, and input/output
  syncobj points. Validate every range, ownership, alignment and role. Build
  Apple work/microsequence structures inside the kernel. Do not accept
  arbitrary META pointers or raw register writes as a permanent UAPI.
- **Scheduling/fences:** `drm_sched`, `dma_fence`, syncobj timeline support,
  BO reservation tracking and bounded queue-space waits. Resolve input
  dependencies on CPU or with verified firmware stamp commands. Signal the
  output fence only on the matching firmware stamp/retirement transition;
  neither IRQ arrival nor work-ring consumption completes a real dispatch.
- **Events/recovery:** threaded IRQ drains bounded events, validates every
  index, decodes stamp bitmaps and keeps fault records. Use polling as a
  diagnostic fallback. A timeout faults the context and all affected fences;
  quarantine in-flight BOs until reset is proven. Explicit reset/reboot is
  preferable to freeing live GPU memory. Add recovery before exposing the
  node to an untrusted compositor.

## What can be reused from powervr

**Verified comparison against this kernel's `drivers/gpu/drm/imagination`:**
`pvr_gem.c`, `pvr_vm.c`, `pvr_context.c`, `pvr_job.c`, `pvr_queue.c`,
`pvr_ccb.c`, `pvr_fw*.c`, `pvr_rogue_fwif.h` and `pvr_rogue_heap_config.h`
provide useful implementation patterns. Its public interface is
`include/uapi/drm/pvr_drm.h`. The two drivers share Rogue/META hardware
concepts, while Apple's channel/ring ABI differs; see
[submission-g5p.md](submission-g5p.md).

| Component | Reuse assessment (proposed) |
|---|---|
| DRM GEM, GPUVM, scheduler, syncobj, dma_fence | Reuse Linux core infrastructure directly; adapt powervr's lifetime/error patterns. |
| META slave-port, MMIO descriptions | Reuse verified access/timeout ideas and matching definitions; retain Apple Fender sequencing and Apple firmware loader/config. |
| GART | Reuse generic allocation/walk concepts. Supply a G5P backend: 16 KiB leaves, recovered directory/catalogue encoding and Apple cache/protection flags. |
| BO/VA/context ioctls | A useful API model. Exact `pvr_drm.h` compatibility requires a field-by-field contract and capability reporting; it is not automatic because both GPUs are Rogue. |
| Upstream CCB/KCCB/FWIF, firmware context structs | Replace with Apple's work-pointer ring, type-2 CL notifications, Apple microsequences, stamps and init objects. Layout copying would be incorrect. |
| Heap constants, reset/SLC sequence, FW binary | Replace with verified Apple values and the pinned C0 LDR. Upstream bases and firmware cannot be substituted. |
| USC/PDS compiler | Userspace workstream owns this. It verified Apple's USC encoding differs from Mesa's; no compiler compatibility claim follows from kernel reuse. |

**Recommendation:** prototype a separate Apple G5P DRM backend sharing core
DRM infrastructure and selected factored helpers; avoid a broad patch that
pretends Apple's firmware is `rogue_fwif`. An upstream powervr backend may
be possible later if its firmware transport and hardware geometry seams
can be factored cleanly. No percentage of source reuse is established.

**First usable milestones:** repeat G2 → dependency/NOP queue transport →
validated context/GART and B → captured C1 compute with matching output and
stamp completion → own shader → DRM BO/VM/submit/syncobj → Mesa integration.
TA/3D parameter management follows compute. The current NOP probe is not a
production submit ioctl and cannot yet execute userspace's captured shader.
