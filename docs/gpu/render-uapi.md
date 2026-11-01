# G5P render UAPI (a9/gpu-render)

2026-10-23. Kernel branch **`a9/gpu-render`** (head in
experiments/render-user.md (`experiments/render-user.md`, not published)), rebased on
`a9/gpu 92d25cc953` (r25). Header: `include/uapi/drm/apple_g5p_drm.h`, exported
to apple_g5p_drm.h (`apple_g5p_drm.h`, not published). This is the interface Mesa (the pvr-fork
G5P winsys, Zink above it) uses to render arbitrary frames once the fixed r22
triangle completes on hardware. **Nothing here has run on a GPU.** All of it
is default-off and privileged, and offline tests only prove that the
constructors reproduce the r22 fixed path byte for byte and that validation,
sequencing and failure handling behave as specified.

Everything is additive: no existing ioctl number, structure or capability
changes, and with the new gates off the compute and fixed-render paths are
unchanged (the r19d/r20/r21 compute and r22/r25 fixed-render suites pass against
this tree; see experiments/render-user.md section 2). One observable
difference with the gates off: `CREATE_CONTEXT` with `type=1` now fails with
`EOPNOTSUPP` instead of `EINVAL`.

**Round 2** (head `a24e938ae1`) adds two more default-off gates, for the
compositor gaps in Mesa round 17
(round17-report.md (`../gpu-userspace/round17-report.md`, not published) section 4):

- `render_user_kernel_geometry`: the kernel derives render-target geometry for
  any size up to 8192×8192 (section 3, "Kernel-derived geometry"). The
  derivation equals Apple's unmodified arithmetic leaf for all 67 108 864
  sizes. The context PM pool grows with the largest target.
- `render_user_dialect2`: profile `TYPED_V2` (section 3, "TYPED_V2"). It adds
  scissor and depth-bias tables, depth/stencil clear values, background
  texture state for LOAD_OP_LOAD, ZLS register images and a pixel output
  width.

With both gates off, every byte and error is as before. Two struct tails
that were reserved, and checked zero on input, now hold outputs that are
written only under the new gate: `CREATE_RENDER_TARGET.reserved[0]` becomes
`pm_blocks`/`pad2`, and `GET_RENDER_INFO.reserved[0..1]` becomes
`max_render_width/height`, `max_pm_blocks` and `pad`. Sizes and every other
offset are unchanged. Mesa's exact diff is in section 8.

Relation to the fixed path: the user path publishes what r22 publishes with
r24's `render_hw_variant=0` (that variant only adds read-only MMIO samples)
and r23's probe 0 or 2 (the W88 value is the typed `vdm_control`, or
`render_user_profile1_vdm_control` for LINEAR64_V1; probes 1/3 replace the
VDM and are TA-only diagnostics, not frame variants). The same rule applies
to r25's latched variants: `render_pm_variant`, `render_pm_blocks`,
`render_memory_variant`, `render_rt_variant=1` and `render_slot_variant` have
`render_user_*` mirrors (section 2) that publish the same bytes. The
diagnostic-only ones (`render_ta_context_variant`, `render_host_variant`,
`render_sku_variant`, `render_rt_variant=2`; the fixed path refuses `pair`
with them) and r25's read-only samples are not offered.

## 1. Model

| Object | Owner | Notes |
|---|---|---|
| VM | file | Existing `CREATE_VM`/`VM_BIND`. A render VM reserves `[64 KiB, 1 GiB)` (root entry 0 borrows the boot VA0 sink), the whole firmware GiB `[0xe1c0000000, 0xe200000000)` and the private window below. |
| GEM BO | file | Existing shmem BOs; READ/WRITE/EXEC roles. Imported dma-bufs are READ\|WRITE. |
| Render context | file | `CREATE_CONTEXT` type `DRM_G5P_CONTEXT_RENDER` (1), flags 0. Exclusive owner of its VM. Two MMU slots (3D first, then TA: 7/8 on a fresh boot), a kernel PM pool (`pm_blocks` × 128 KiB of physical pages, the 0x10680-byte aux block), both TA/3D channels, stamps and queues. |
| Render target | file handle, context memory | `CREATE_RENDER_TARGET`. Kernel RT main ring and secondary buffer mapped in the context's private window. The HWRT data set of this firmware. |
| Job | context | One TA (geometry) + one normal 3D (fragment) pass: `SUBMIT_RENDER` (profile LINEAR64_V1) or `SUBMIT_RENDER_TYPED` (TYPED_V1). |

Private window: `[0x101000000, 0x102000000)`, reported by `GET_RENDER_INFO`.
The PM aux block sits at its start and the first 64×64 target at
`0x101020000`/`0x101030000`, exactly where r22 put them, so a 64×64 job
produces the same W anchors as the qualified fixed path. Private pages are
not VM maps: `GET_CONTEXT_STATUS` still counts only the caller's maps
(round16 requires 12 maps, 0x16c000 bytes for its client).

**One render attachment per boot.** TA/3D channel, PM control and PM
list/free pages are device-global firmware objects (shared+0x54000..0x5ffff
and the two `render_meta` pages), shared with compute's work page. The first
accepted job claims the device (same exclusions as compute and r22: no
compute owner, no fixed render, no CL/attach probe, policy-7 epoch); compute,
probes and diagnostic sysfs commands stay blocked until reboot, also after a
successful detach. Inside that context jobs are unlimited and serialized.

## 2. Capabilities and gates

| Gate (`a9_gpu_drm.`) | Caps | Notes |
|---|---|---|
| `render_user_experiment` | `RENDER` (5), `BOUNDED_RENDER` (19), `TYPED_RENDER` (20), plus `VM_LIVE`, `CONTEXT_LIVE`, `CODE_BASE_VERIFIED`, `code_base=0x300000000` | **Set before firmware map**: it also maps the PM list/free pages (like `render_fixed_experiment`). `GET_EXEC_INFO` then reports the common USC/PDS base 0x300000000, VM scope, 32-bit offsets. |
| `render_user_geometry` | `RENDER_USER_GEOMETRY` (21) | Accept userspace RT geometry. With `render_user_kernel_geometry` it must equal the kernel's derivation. |
| `render_user_kernel_geometry` | `RENDER_KERNEL_GEOMETRY` (22) | Round 2. Kernel-derived RT geometry, 1..8192 per axis; per-size PM pool growth; `GET_RENDER_INFO.max_render_*`, `max_pm_blocks`; `CREATE_RENDER_TARGET.pm_blocks`. Checked at each `CREATE_RENDER_TARGET`. |
| `render_user_dialect2` | `TYPED_RENDER_V2` (23) | Round 2. `SUBMIT_RENDER_TYPED` profile `TYPED_V2` (3), `GET_RENDER_INFO.profiles` bit 3. Checked at each submit. |
| `prime_experiment` | `PRIME` (6) | dma-buf export/import. The DRM core always reports `DRM_CAP_PRIME`; use this bit. |

Core DRM caps Mesa reads: `DRM_CAP_SYNCOBJ`, `DRM_CAP_SYNCOBJ_TIMELINE` (1),
`DRM_CAP_PRIME` (import|export). All ioctls need `CAP_SYS_RAWIO`
(see section 9).

Latched per context at `CREATE_CONTEXT` (module parameters; they mirror the
fixed path's experiment variants and are the open hardware hypotheses):

| Parameter | Default | Meaning |
|---|---|---|
| `render_user_stage` | 0 | 1 attach only, 2 TA only (one-shot, job ends `-ECANCELED`). Device ladder only. |
| `render_user_cache_variant` | 3 | r22 cache preamble before the first job (0 or 3). |
| `render_user_mmu_variant` | 1 | 0 verify DC3 CBASE; 1 rewrite CBASE, then slot invalidate. |
| `render_user_timestamp_variant` | 0 | 1 = equal-length zero-mask waits instead of timestamp packets. |
| `render_user_3d_after_ta` | 0 | Host waits TA retirement before notifying 3D (tag4 still published). |
| `render_user_wait_ms` | 12000 | Per-job TA+3D bound, 3000..30000. |
| `render_user_pm_policy` | 0 | Jobs ≥ 2: 0 rebuild pool/list/F, keep state record S; 1 rebuild all. |
| `render_user_cache_repeat` | 0 | Repeat the cache preamble + slot invalidates before every job. |
| `render_user_pm_blocks` | 32 | 1..128 blocks (4 MiB default; 128 = 16 MiB = one F page). r25's `render_pm_blocks=64/128` is this value at 64/128. With `render_user_kernel_geometry` this is the pool's floor: larger targets grow it (section 3), so an r25 qualification at 32/64/128 describes the latched value, not necessarily the effective pool (logged at attach: `RENDER-USER attached ... blocks=N`). |
| `render_user_profile1_vdm_control` | 0x280 | LINEAR64_V1's TA W88 (r22's 0x280 or captured 0x100, r23 probe bit1). |
| `render_user_pm_variant` | 0 | r25 `render_pm_variant`: 1 firmware rebuilds F/count/last from an owned head holder named by tag6+10 (head, count, F cleared, last=ffffffff, every job); 2 reverse only F's block order; 3 F extent = its whole 16 KiB page. Reapplied after each pool rebuild. |
| `render_user_memory_variant` | 0 | r25 `render_memory_variant`: 1/2/3 the five PM-aux leaves get flags 0/4/8 instead of c; 4 every render-VM leaf gets 0: PM aux, RTs and all VM_BIND maps (native and imported). Mode 4 needs a VM with no maps at `CREATE_CONTEXT` (else `EINVAL`). Borrowed firmware/sink entries are never edited. |
| `render_user_rt_variant` | 0 | r25 `render_rt_variant=1`: TA.W28 and 3D.W20 point one slice (`header` rounded to 4 KiB; 0x1000 for 64×64) into the target's main ring. Value 2 is refused. |
| `render_user_slot_variant` | 0 | r25 `render_slot_variant`: 1 = TA takes the lower free user slot (7) and 3D the next. DC3 publication stays 3D then TA. |

## 3. Ioctls

All structures are fixed-width with u64 user pointers; 32-bit callers
zero-extend. Reserved/pad/output fields are zero on input. Sizes and offsets
are compile-checked for ARM32 and ARM64 (`tools/gpu/test_render_uapi_layout.c`).

| Nr | Ioctl | Struct (bytes) |
|---|---|---|
| 0x07 | `CREATE_CONTEXT` type 1 | `drm_g5p_create_context` (16), unchanged |
| 0x08 | `DESTROY_CONTEXT` | unchanged; render: idle DC3 unbind (below) |
| 0x10 | `SUBMIT_RENDER` | `drm_g5p_submit_render` (64), round16 layout byte for byte, `0xc0406450` |
| 0x11 | `CREATE_RENDER_TARGET` | `drm_g5p_create_render_target` (200) |
| 0x12 | `DESTROY_RENDER_TARGET` | `drm_g5p_destroy_render_target` (8) |
| 0x13 | `GET_RENDER_INFO` | `drm_g5p_get_render_info` (120) |
| 0x14 | `SUBMIT_RENDER_TYPED` | `drm_g5p_submit_render_typed` (80), `0xc0506454`; pass `drm_g5p_render_pass` (360, TYPED_V1) or `drm_g5p_render_pass_v2` (488, TYPED_V2) |

### CREATE_CONTEXT, type RENDER

`{vm, type=1, flags=0, handle=0}` → handle. Errors: `EOPNOTSUPP` gate off,
`EPERM` no `CAP_SYS_RAWIO`, `ENOENT` VM, `EBUSY` VM already has a context,
`EINVAL` the VM already maps something in a reserved range (or its root
entries 0 / firmware GiB are in use; any map at all with
`render_user_memory_variant=4`; or an invalid latched parameter), `ENOSPC` no two free slots, `ENOMEM`.
Allocates the PM pages (default 4 MiB) and the aux block; touches no
hardware. Create the render context before mapping BOs (or map only above
`user_va_min`, outside the private window).

### CREATE_RENDER_TARGET

| Field | Dir | Meaning |
|---|---|---|
| `context` | in | Render context; RT memory is mapped into its VM. |
| `flags` | in | `DRM_G5P_RT_USER_GEOMETRY` or 0. |
| `width`, `height` | in | Pixels. Kernel geometry: 1..8192 (`render_user_kernel_geometry`, else `EINVAL`); without that gate only 64×64 (the executed 6ca6d5c table), else `EOPNOTSUPP`. USER geometry: 1..16384. |
| `samples`, `layers` | in | Must be 1. |
| `handle` | out | File-scoped handle. Holds a reference to its context. |
| `main_va`, `main_bytes` | out | Main ring: power of two ≥ 16 × slice, slice = `header_bytes` rounded to 4 KiB (6cb3d68). |
| `secondary_va`, `secondary_bytes` | out | Secondary buffer (the tail pointer cache), right after main, 16 KiB pages. |
| `geometry` | in (USER) / out | `drm_g5p_rt_geometry`, the AGXRenderTargetMemoryG5 outputs below. Zero on input unless USER. |
| `pm_blocks` | out | Round 2, `render_user_kernel_geometry` only (else 0): the context's PM pool in 128 KiB blocks after this target. Zero on input, as are `pad2` and `reserved`. |

`drm_g5p_rt_geometry` (firmware round16 section 3 R-offsets) and where the
kernel writes each value (`g5p_ru_rt_fill`):

| Field | R | 64×64 value | TA W | 3D W |
|---|---|---|---|---|
| `header_bytes` | 10 | 0x500 | – (sizes the slice/main ring) | – |
| `region_bytes` | 20 | 0x50 | – | 38, a8 (= R20>>2) |
| `packed_state` | b0 | 0x88 | – | 28, 58 |
| `state_word` | b8 | 0 | – | 30 |
| `padded_dims` | bc | 0x40004 | – | 34, 3a0 |
| `scale[3]` | c0/c8/d0 | 3b89309c3b89309c, 3cddb3d93cddb3d9, 4480000044800000 | – | 3c/44/4c, 3d0/3d8, 3e0 (low Rd0), 3e4 (low Rc0) |
| `ta_state[2]` | f0/f8 | 0, 0x88 | 38, 40 | – |
| `ta_tiling[2]`, `ta_tiling_word` | 100/108/110 | 00001001003f003f, 0010100c0010100c, 0x10 | 3ac, 3b4, 3bc | – |
| `tile_count` | 118 | 4 | – (bounded by ⌈w/32⌉×⌈h/32⌉) | – |
| `secondary_bytes`, `secondary_align` | 138/178 | 0x800, 0x40 | 44c | – |
| main / secondary VA | – | – | 28 / 30, 444 | 20 |

USER geometry bounds: header 1..0x80000 (main ≤ 8 MiB), secondary
1..1 MiB, align a power of two ≤ 16 KiB, tiles ≥ 1. The 3D W58/Wa8 mapping
is the importer literal for the verified case; for other geometries it is
derived by analogy and unqualified. With `render_user_kernel_geometry` the
USER values must equal the kernel's derivation (below) field for field, so
`EINVAL` otherwise: under that gate every published RT word is Apple's
arithmetic. Errors: `EINVAL` fields, `EOPNOTSUPP` size/gate, `EBUSY` context
busy or core lock contended, `ENOSPC` 32 targets or window full, `ENOMEM`
(also PM growth), `EIO` context lost.

#### Kernel-derived geometry (round 2, `render_user_kernel_geometry`)

`render-user-geometry.h` computes the leaf `6ca6d5c` for one sample at the
pixel centre (8,8), with integer arithmetic only. For width w and height h:

| Output | Formula | Rogue counterpart (Mesa pvr) |
|---|---|---|
| tiles x/y | tx = ⌈w/32⌉, ty = ⌈h/32⌉; `tile_count` (R118) = tx·ty | `num_tiles_x/y` |
| tiles per macrotile | mx = ALIGN(⌈tx/4⌉, 4), my likewise; `padded_dims` (Rbc) = mx<<16 \| my (10-bit fields) | `mtile_x1/y1`, ISP_MTILE_SIZE |
| `ta_tiling[0]` (R100) | ((ty−1)<<12 \| (tx−1))<<32 \| (h−1)<<16 \| (w−1) | `x/y_tile_max`, pixel maxima |
| `ta_tiling[1]` (R108) | per axis m<<18 \| (2m)<<9 \| 3m (9-bit fields), y in the high dword | `mtile_x1..x3` |
| `ta_tiling_word` (R110) | mx·my | tiles per macrotile |
| `header_bytes` (R10) | (4mx)·(4my)·5 | region headers, 5 bytes each (`rogue_get_region_header_size`) |
| `region_bytes` (R20) | mx·my·5; 3D Wa8 = R20>>2 → ISP_RGN (CR f28) | `pvr_rt_get_isp_region_size` |
| `secondary_bytes` (R138) | next_pow2(max(4mx, 4my))²·8, align 0x40 (R178) | tail pointer cache, `ROGUE_TAIL_POINTER_SIZE` 8 |
| `scale[0..2]` (Rc0/c8/d0) | IEEE single 0x3e89309c/n, 0x3fddb3d9/n (fdiv, round to nearest even) and n·16, x low, y high | – |
| `packed_state`, `ta_state`, `state_word` | 0x88, {0, 0x88}, 0 (one sample at 8,8) | – |

Evidence: `tools/gpu/test_rt_oracle.c` links the unmodified leaf at its
kernelcache address (`rt_oracle_leaf.S`, hash-pinned `rt-leaf.bin` and
`rt-constants.bin` from `round16-evidence.py --rt-oracle`). It runs that leaf
in an ARM64 process for every (w, h) in 1..8192 × 1..8192 (67 108 864 sizes,
16 leaf fields each) and gets 0 mismatches against the kernel function. It
also checks nine relations against Mesa pvr's Rogue helpers
(`pvr_arch_rt_mtile_info_init`, region-header, tail-pointer and ISP-region
sizes) and the integer fdiv against the host's IEEE division. Derived 64×64
equals the verified table, so 64×64 bytes cannot change.

The limit of 8192 is `rogue_get_render_size_max()` for this tiling class. The
5-byte headers and 4×4 macrotiles show this is a non-simple-parameter-format
core. It is a memory bound, not a field bound. The worst case is 6145×7681
and up: an 8 MiB main ring plus a 512 KiB tail pointer cache, inside the
16 MiB private window, with header ≤ `G5P_RU_MAX_HEADER`. 16384 would need a
32 MiB main ring. The TE (9-bit) and ISP (10-bit) fields would still hold it.

| Target | tiles | header | main ring | TPC (secondary) | padded | PM blocks (latched 32) |
|---|---|---|---|---|---|---|
| 64×64 | 4 | 0x500 | 0x10000 | 0x800 (map 0x4000) | 0x40004 | 32 |
| 100×75 | 12 | 0x500 | 0x10000 | 0x800 | 0x40004 | 32 |
| 1536×2048 (scanout) | 3072 | 0x3c00 | 0x40000 | 0x8000 | 0xc0010 | 98 |
| 2048×1536 (rotated) | 3072 | 0x3c00 | 0x40000 | 0x8000 | 0x10000c | 98 |
| 1920×1080 | 2040 | 0x3c00 | 0x40000 | 0x8000 | 0x10000c | 66 |
| 4096×4096 | 16384 | 0x14000 | 0x200000 | 0x20000 | 0x200020 | 128 |
| 8192×8192 | 65536 | 0x50000 | 0x800000 | 0x80000 | 0x400040 | 128 |

1536×2048 scale words: 3909309c3936eb7b, 3a5db3d93a93cd3b, 4700000046c00000.
Targets are allocated in `G5P_RU_RT_ALIGN` (64 KiB) units from the 16 MiB
window; a compositor's two full-screen targets plus window-sized ones fit
(each full-screen target is 0x50000 bytes).

**PM pool policy** (Linux policy, not hardware). The pool holds Rogue's
minimum free list (50 4 KiB pages, `pvr_srv_get_min_free_list_size()` for
non-XE cores) plus one 4 KiB PM page per tile, in 128 KiB blocks. It is never
below `render_user_pm_blocks` and never above one F page (128 blocks,
16 MiB). Each `CREATE_RENDER_TARGET` under the gate grows the context's pool
(`kvcalloc` of the new array, pages cleaned to PoC when attached). This is
allowed only while the context is idle, the same rule as target creation.
The next attach, or the next job's pool rebuild (both PM policies), builds
control, list and F with the new count. S is not block-dependent. The pool
never shrinks. There is still no PM growth during a job (tag5 is
unimplemented): a frame that exhausts the pool fails like any PM OOM.

`DESTROY_RENDER_TARGET {handle, flags=0}`: idle context only (`EBUSY`);
unmaps (with tag publication + invalidate when attached) and frees.

### GET_RENDER_INFO

`size` = 120 exactly, everything else zero in. Returns `profiles` (bit 1
LINEAR64_V1, bit 2 TYPED_V1, bit 3 TYPED_V2 with `render_user_dialect2`),
`max_render_targets` 32, `private_va_start/end`, `user_va_min` 0x40000000,
`usc_base = pds_base = 0x300000000`, `code_window` 4 GiB (32-bit offsets),
`pm_blocks` (the latched floor), `pm_block_bytes` 0x20000, `max_queued_jobs`
16, `max_vdm_bytes` 0x10000, `job_timeout_ms`, `retained_results` 1024, and
with `render_user_kernel_geometry` `max_render_width = max_render_height =
8192`, `max_pm_blocks` 128 (otherwise 0: 64×64 only). `EOPNOTSUPP` with the
gate off.

### SUBMIT_RENDER (0x10, LINEAR64_V1)

Round16's proposal unchanged (render-uapi-r16.md (`../gpu-userspace/render-uapi-r16.md`, not published)):
exactly 12 canonical resources `{handle, kind=index, offset=0, size=whole BO,
va}` at the fixed VAs, the round16 BO roles, and SHA-256-exact immutable
contents (code/static/state/quad/ppp/scissor/vdm/vertices; target, scratch
and pools are mutable). The kernel translates them to the TYPED_V1 pass in
`render-user-profile1.h` (generated from the round16 manifest and record),
with W88 from `render_user_profile1_vdm_control`, and creates an implicit
64×64 target. `drm-triangle-r16.c` and the round16 device package run
unmodified.

### SUBMIT_RENDER_TYPED (0x14, TYPED_V1 and TYPED_V2)

| Field | Meaning |
|---|---|
| `context` | Render context. |
| `flags` | 0. |
| `profile` | `DRM_G5P_RENDER_PROFILE_TYPED_V1` (2), or `_TYPED_V2` (3) with `render_user_dialect2` (with the gate off, 3 is an unknown profile: `EINVAL`). |
| `render_target` | Handle from `CREATE_RENDER_TARGET` of this context. |
| `resources`, `resource_count` | 1..256 `drm_g5p_render_resource`: every BO the pass references. |
| `syncs`, `sync_count` | 1..17 `drm_g5p_sync`: exactly one SIGNAL, ≤ 16 inputs (binary point 0 or timeline point). |
| `pass`, `pass_size` | TYPED_V1: `drm_g5p_render_pass`, `pass_size` = `pass.size` = 360. TYPED_V2: `drm_g5p_render_pass_v2`, `pass_size` = `base.size` = 488. |
| `job_id` | out. |

`drm_g5p_render_resource` for TYPED_V1/V2: `[va, va+size)` must lie in one VM
mapping of `handle` with `offset` = that mapping's BO offset at `va`. `kind`
is an access mask:

| Bit | Name | Meaning |
|---|---|---|
| 0 | `ACCESS_READ` | GPU reads; BO role READ required. |
| 1 | `ACCESS_WRITE` | GPU writes; BO role WRITE required. |
| 2 | `ACCESS_IMPLICIT` | Wait for the BO's `dma_resv` fences (a writer waits for readers+writers, a reader for writers; our own context's fences are skipped) and add the job fence with WRITE/READ usage. |
| 3 | `ACCESS_CACHE` | Kernel whole-BO CPU cache maintenance: `dma_sync_sgtable_for_device` before the job, `..._for_cpu` after full TA+3D retirement and **before** status and fence. |

At least one of READ/WRITE; unknown bits `EINVAL`; duplicate handles
`EINVAL`. **An imported (foreign dma-buf) BO always gets IMPLICIT and
CACHE**, on the attachment's sg_table: this is the simpledrm scanout
requirement (`docs/omarchy/gpu-compositor-plan.md` items 2 and 3).

### TYPED_V1 render pass: field map

Every user VA and every code-relative descriptor of the r22 W templates is a
field here; `make-render-user-template.py` proves it by perturbing each input
of the hash-pinned r22 importer (record → D → W) and checking that exactly
these W words change, and that no other template word holds a user VA.

| Field | Check | TA W | 3D W | iOS source |
|---|---|---|---|---|
| `vdm_va`, `vdm_size` | READ range, 4-aligned, 4..0x10000 | 98 (`&~3`) | – | record+38/+40 |
| `vdm_control` | multiple of 16, ≤ 0x10000 | 88 (→ R0+470) | – | record+40 (RenderContext+0x38; r23 open question) |
| `scratch_va`, `scratch_size` | RW range, 16-aligned, 16 B..64 MiB | 90 | – | record+1d8 |
| `scratch_ptr[0..3]` | 4-aligned, RW | 468/470/478/480 | – | record+48/50/58/60 |
| `scratch_ptr[4]`, `[5]` | 4-aligned, RW | 48 and 488, 50 | – | record+98, +a0 |
| `pool7_va/size`, `pool8_va/size` | page-aligned RW ranges | – | 68+490, 60+4a0 | record+2e0, +2d8 |
| `context_pds[0][6]`, `[1][6]` | opaque | 58..6c, 70..84 | – | record+68, +80 |
| `resume_pds[0][4]`, `[1][4]` | opaque | a0..ac, b0..bc | – | record+a8, +c0 |
| `ta_pds[0..2]` (`[3]`=0) | opaque | c0/c4/c8 | – | record+190 |
| `background[8]` | opaque | – | b0,b4 (0,1), c0..cc (4..7), 5b8..5d4 (all) | record+288 |
| `eot[0..2]` (`[3]`=0) | opaque | – | 3b8/3b4/3bc (first qword swapped), 680..688 | record+2b8 |
| `eot_resume[0..2]` (`[3]`=0) | opaque | – | 694..69c | record+530..538 |
| `restore[4]` | opaque | – | 600, 604, 610, 614 | record+518/51c/528/52c |
| `reserved[8]` | zero | | | |

"Opaque" descriptor words are GPU-interpreted PDS/USC descriptors whose code
and data offsets are 32-bit and relative to the 0x300000000 base, so the
programs they name are confined to `[0x300000000, 0x400000000)` of the
caller's VM. The kernel does not decode them.

**Frozen dialect-1 scalars** (color only, one sample, normal 3D, no depth,
no resource arrays): TA W20/24 = 1e3ce508, W3a4 = 88, W3a8 = 2, W3c0 =
0x20000, W514 = 08002358, W528 = 1; 3D W108..130 = 3c3 (six), W3a4/6a8 =
3f800000, W3a8/6ac = 300, W3b0 = 1f, W3c0..3cc = 21000f/408/8/f8, W6b0 = 2,
W868 = 08002358 (`render-user-template.h`). Depth/stencil attachment slots
(3D W80..a4, W138 and copies), the 3D copied-state and helper blocks and the
TA mode bytes stay zero/fixed in TYPED_V1; TYPED_V2 below types the ones with
an identified register. MSAA, multiview and resource arrays still need a
later dialect.

### TYPED_V2 (round 2, `render_user_dialect2`)

`drm_g5p_render_pass_v2` = `drm_g5p_render_pass base` (360 bytes, TYPED_V1
layout byte for byte) + `drm_g5p_render_pass_ext ext` (128 bytes). In V2,
`base.pool7_*` and `base.pool8_*` must be zero: the named scissor and
depth-bias tables replace them. Every other `base` field is validated and
placed exactly as in TYPED_V1, and `base.reserved` and `ext.reserved` are
zero. The kernel builds the TYPED_V1 W, then writes each V2 register image to
its 3D B/A-upload word and to the normal builder's copy (`6c7a06c`), the
same words the iOS importer fills. A V2 pass that restates V1 (tables =
pools with 65536 entries each, clears 1.0f/0x300, width 2, no ZLS) publishes
V1's bytes exactly (`test_render_user.c`).

**How positions and registers are known.** `make-render-user-dialect2.py`
does two things. (1) It perturbs each Apple record field behind a V2 field
through the hash-pinned r22 importer (record → D → W). Exactly the two
listed 3D words change, and no TA word does. (2) It follows the data flow
of the firmware's 3D upload (`round6-firmware-extract.txt`, A = W+3a0,
B = W+58) from each `GETD` to the `SETD`/`SETL` on the register base `D1Ar5`.
W3c0 lands on 0xf38, which is `ROGUE_CR_ISP_CTL` in Linux's
`pvr_rogue_cr_defs.h` (the check script greps it). That anchors the base, so
the other offsets are Rogue CR offsets. The Rogue *names* come from the
offsets, Rogue `cr.xml` semantics and value checks, and are graded:

| `ext` field | Check | 3D W (direct, copy) | CR | Rogue register | iOS record | Grade |
|---|---|---|---|---|---|---|
| `scissor_va`, `scissor_count` | READ, 4-aligned, 1..65536 entries × 8 B | 60 (`&~3`), 4a0 | fb0 | ISP_SCISSOR_BASE | +2d8 ("pool8") | **strong**: Apple's pool is 0x80000 = 65536 × 8 B (`IPF_SCISSOR_WORD_0/1`), the 16-bit `ISPDBSC.scindex`; `&~3` is the [39:2] address field; adjacent to DBIAS |
| `dbias_va`, `dbias_count` | READ, 4-aligned, 1..65536 entries × 12 B | 68 (`&~3`), 490 | fb8 | ISP_DBIAS_BASE | +2e0 ("pool7") | **strong**: 0xc0000 = 65536 × 12 B ({constant, slope, clamp} f32, Mesa `pvr_depth_bias_state`), 16-bit `dbindex` |
| `bgobj_depth` | any 32 bits | 3a4, 6a8 | f80 | ISP_BGOBJDEPTH | +4d0 | **strong**: captured 0x3f800000 = 1.0 clear depth |
| `bgobj_vals` | bits [9:0] only | 3a8, 6ac | f88 | ISP_BGOBJVALS | +4d4 | **strong**: captured 0x300 = enablebgtag \| mask, stencil 0 |
| `background_texture` | opaque (B-relative like `background[]`) | b8, 5f0 | 6a8 | PDS_BGRND1_BASE (texture/varying data) | +508 | **good**: 6a0/6a8/6b0/6b8 take bg[0..1], this, bg[4..5], bg[6..7]: BGRND0..3 in Rogue order; bg[0..1] decodes as shader/texunicode offsets 28c0/2840, bg[6..7] as a SIZEINFO |
| `zls[0]` | opaque; address if flagged | 80, 618 | f48 | one of ISP_ZLSCTL / ZLOAD / ZSTORE / STENCIL_LOAD / STENCIL_STORE | +2f0 | position proven; identity and order **hypothesis** |
| `zls[1]`..`zls[4]` | same | 88/628, 90/630, 98/648, a0/650 | f50, f58, f60, f68 | same set | +310, +338, +360, +388 (heads of four 0x28-byte groups) | as above |
| `zls[5]` | same | 138, 6d0 | fd0 | unknown (ZLS-adjacent) | +308 (round16: "depth field") | position proven only |
| `pixel_output_width` | 2, 4 or 8 | 3c8, 3cc, 108..130 (six per-core), 6b0 | fd8, …, indexed | ISP_CTL partition count / USC pixel output | +6dc (RT parameter dword 19, D2f8) | **formula exact** (6ca7ae0 + kernelcache table 5942678 = {0,0,0x10,0,0,0,0x20}); meaning (output registers per pixel) inferred |
| `zls_address_mask` | bits 0..5 | – | – | – | – | kernel-only: which `zls[i]` are addresses |

Pixel output words (W3c0 = 0x21000f and W3c4 = 0x408 for all three, because
the partition count stays ≥ 2):

| width | W3c8 | W3cc | W108..130 (each) | W6b0 |
|---|---|---|---|---|
| 2 (one 32-bpp colour, captured) | 8 | f8 | 3c3 | 2 |
| 4 | 4 | f9 | 1d3 | 4 |
| 8 | 2 | fa | e3 | 8 |

Validation (TYPED_V2, before acceptance, `EINVAL`): the V1 rules except the
pools. The pools must be zero. Both tables must have 1..65536 whole entries,
word aligned, inside one listed READ range. Width must be 2/4/8.
`bgobj_vals` must fit [9:0] and the mask bits 0..5. Each flagged `zls[i]`
must have [63:40] zero, and its 16-byte-aligned address ([39:4]) with 16
bytes must lie inside a listed READ|WRITE range. Bits [3:0] are left to the
register (e.g. a stencil-base enable). Unflagged `zls` words are opaque.
The kernel does not decode ZLSCTL, so it cannot bound the per-tile ZLS
footprint. As with shader addresses, the extent is the caller's
responsibility: the ABI is privileged and the MMU is the boundary.
`render_user_dialect2` is read at each submit. A V2 job behaves exactly like
a V1 job from acceptance on (queue, PM, retirement, failure retention).

**What the fields enable** (all unqualified on hardware):
- *Scissor table*: Mesa's recorder writes `IPF_SCISSOR_WORD_0/1` pairs and
  `ISPDBSC.scindex`, but before V2 had no base. `scissor_va` is that base.
  `ISPCTL.scenable` and `dbenable` stay in the userspace PPP words.
- *Depth/stencil*: on-chip depth or stencil with any clear value
  (`bgobj_depth`/`bgobj_vals`; ZLS all zero = no load/store, "depth stays on
  chip"). Load/store of a depth buffer needs `zls[]`, whose order is a
  device-ladder question (section 4 of experiments/render-user.md).
- *LOAD_OP_LOAD*: the background object is a PDS program that reads the
  previous contents as a texture. `background[]` (V1) names the program and
  its uniforms, `background_texture` its texture state (BGRND1), and the
  target must be listed with READ as well as WRITE.
- *Multiple colour attachments*: the EOT program (userspace PBE emits)
  plus `pixel_output_width`. Which width a given attachment set needs is
  inferred (2 for one 32-bpp colour is captured), so MRT is unqualified.
- *Formats*: the kernel has no format state. B8G8R8A8/R8G8B8A8 UNORM/SRGB
  differ only in the userspace PBE/EOT words in the state BO: the PBE
  keys, and the sRGB gamma bit, which still needs capture evidence on the
  Mesa side. All 32-bpp formats use width 2 (inference consistent with the
  captured BGRA8). No kernel change is needed per format.

Not typed (context only): W58 → fa8 (RT sample state Rb0), Wa8 → f28
(ISP_RGN = R20>>2), W3a0 → f18 (ISP_MTILE_SIZE = Rbc), W3b4..3bc →
618..628 (EOT, EVENT_PIXEL_PDS_*), W3d0..3e4 → 11d0..11f8 (RT scales),
Wd0 → f30 (D6e0, AA mode, 0 for one sample), W70/W78 (per-partition,
D318 & ~f: probably ISP_OCLQRY_BASE, asserted zero), Wd8..100 (per-core
resource array, disabled). The full W→CR table is
`build/gpu/render-user/dialect2-map.json`.

**Kernel-owned** in every job: TA/3D channel fields (W0/4/c/10/14/18/1c,
W454/458, W52c..544, W888..8a0), RT anchors, the full timestamped SKU
microsequences (TA 23/24 wait 801; 3D 25/26 with the 0x88-byte
two-partition wait), the tag6 PM bootstrap and tag4 TA-finish dependency,
pointer/global rings and stamps.

### Validation and errors (both submit ioctls)

Before acceptance, in this order: gate (`EOPNOTSUPP`), privilege/boot gate
(`EPERM`), epoch (`EAGAIN` not alive, `ESHUTDOWN`, `EIO` failed), copies
(`EFAULT`, `ENOMEM`), fields (`EINVAL`), syncobjs (`ENOENT` handle, `EINVAL`
rules, `E2BIG` > 16 inputs), context (`ENOENT`, `EINVAL` not render, `EIO`
lost/detached, `EBUSY` 16 queued), resources (`ENOENT`, `EINVAL`),
LINEAR64_V1 bytes (`EINVAL`), render target (`ENOENT`), pass ranges
(`EINVAL`), retained-result slot (`EBUSY`), device claim (`EBUSY`).
**Every one of these leaves no output-fence replacement, no queued work and
no hardware access.** After acceptance the ioctl only installs the fence,
attaches `dma_resv` fences and queues; `EFAULT` on copy-out can follow
acceptance (check the syncobj/`GET_JOB_STATUS`). A `dma_resv` failure after
acceptance fails the job (error fence) without touching hardware.

Sync rules are compute's: one SIGNAL; inputs need an existing fence (no
WAIT_FOR_SUBMIT); binary wait+signal on one handle is rejected; a timeline
input on the signal handle must be below the output point; a timeline
output needs a chain payload below its point; duplicates rejected.

## 4. Execution

Jobs go to an ordered queue (≤ 16 per context). For each job the worker
waits for inputs and implicit dependencies under one 10 s deadline, takes the
command lock with a bounded 2 s retry, then:

1. **First job only — attach.** Borrow the firmware-GiB and VA0-sink
   catalogue entries into the user root; build the PM object (control/S,
   list, F from the physical pages); initialize both channels; clean every
   table, aux, RT, PM page and the firmware pages to PoC; publish
   `PA[slot]=root`, the packed generation tag, and two DC3 entries (3D, then
   TA) with one kick; wait for consumption; require host and cached tags
   equal, verify/rewrite CBASE and invalidate both slots; require
   f088/f090 = 30102/60405.
2. **PM.** Job 1 uses the fresh object. Jobs ≥ 2 rebuild control+0..7f, the
   list and F (policy 0, S kept; S+0 keeps counting) or everything
   (policy 1), always with the context's current block count (grown by
   `CREATE_RENDER_TARGET` under `render_user_kernel_geometry`).
3. **Objects.** Per engine: W = frozen scalars + typed fields (+ TYPED_V2
   register images, direct and copy) + RT anchors +
   channel fields with stamp target and sequence `seq`; fresh SKU; tag6
   `{6,STA,0,M(control)}` / tag4 `{4,M(TA.W530 word),seq,seq}`; pointer-ring
   entries at `2(seq−1) mod 16`, endpoint `2·seq mod 16`.
4. **Publish.** Cache preamble + slot invalidates on job 1 (or every job with
   `cache_repeat`). TA: global entry, channel producer, global producer,
   kick 0x10, each after a barrier; 3D likewise with kick 0x11, immediately
   (tag4 makes firmware wait) or after observed TA retirement.
5. **Retire.** Both stamp words = `seq`, W pointers intact, all five
   watermarks = endpoint, global ring drained, runtime globals idle, status
   (430.0 set and .1 clear; 1090.0), selectors = slot×0x101, f078 bytes =
   slots, TA PM save present, 3D S+0 incremented, no MMU fault/event.
   Stamps beyond `seq` or watermarks ≥ 16 are protocol errors.
6. **Cache, status, fence.** `ACCESS_CACHE` BOs synced for CPU, then status
   (`GET_JOB_STATUS` COMPLETE, error 0) under the status lock, then the fence.

Any hardware-stage failure (timeout, fault, protocol, bad PM/S state) marks
the context lost and the boot failed, snapshots CH/W/SKU/PM/global state and
registers into `diagnostic_blob`/`mmu_history`, and retains every page, slot
and binding until reboot. There is no reset, retry, eviction or in-job PM
growth.
Queued jobs behind a failure complete with `-EIO` without hardware.
Input/implicit-dependency errors fail only that job.

VM edits on a live render VM (`VM_BIND`, `CREATE/DESTROY_RENDER_TARGET`) are
allowed only while no job is queued: the kernel publishes the new tag to
both slots and invalidates both (round15 rule, no DC3). Counters that would
wrap fail closed (`EOVERFLOW`, context lost).

`DESTROY_CONTEXT` on an idle, healthy, attached render context zeroes both
slots' PA/tag and consumes two DC3s, then unmaps private pages; memory is
freed when the last reference (render-target handles, jobs) drops. A context
that claimed the device but never attached is released and the claim
dropped. Failure, a lost context or fd close without destroy retains
everything until reboot.

Results: `GET_JOB_STATUS` per file; the oldest completed result is evicted
beyond 1024. `GET_CONTEXT_STATUS` reports LIVE (3) after attach, LOST (1)
after a failure.

## 5. PRIME (`prime_experiment`)

- **Export**: dma-buf with the shmem vmap/mmap (write-combined when the
  device is not coherent) plus `begin_cpu_access`/`end_cpu_access` doing
  `dma_sync_sgtable_for_cpu`/`for_device` on the G5P mapping, so a CPU
  importer (simpledrm blit, llvmpipe) sees GPU writes. Re-importing our own
  export returns the same GEM handle.
- **Import**: `drm_gem_prime_import` → attachment mapped for the GPU and
  pinned for the GEM object's life → shmem GEM with roles READ|WRITE. Size
  must be whole 16 KiB pages and ≤ 64 MiB. `VM_BIND` maps the attachment's
  sg_table pages (16 KiB, identity-DMA-checked; no contiguity required).
- **Sync**: job fences are real `dma_fence`s. Export a sync_file with the
  core `DRM_IOCTL_SYNCOBJ_HANDLE_TO_FD` (`EXPORT_SYNC_FILE`) or from a
  dma-buf with `DMA_BUF_IOCTL_EXPORT_SYNC_FILE`; import with
  `SYNCOBJ_FD_TO_HANDLE` or `DMA_BUF_IOCTL_IMPORT_SYNC_FILE`. With
  `ACCESS_IMPLICIT` (forced for imports) the kernel also adds the job fence to
  the BO's `dma_resv`, which simpledrm's `prepare_fb` waits on.

## 6. What Mesa must send

1. `GET_INFO`: require `RENDER|BOUNDED_RENDER|TYPED_RENDER|VM_LIVE|
   CONTEXT_LIVE|SYNCOBJ|JOB_STATUS|CONTEXT_STATUS`, BVNC 9.15.6.2; `PRIME`
   for WSI/kmsro; `RENDER_KERNEL_GEOMETRY` (22) for any target other than
   64×64 and `TYPED_RENDER_V2` (23) for scissor/depth/LOAD/clear values.
   `GET_EXEC_INFO` + `GET_RENDER_INFO`: base 0x300000000, 32-bit offsets,
   `user_va_min`, private window, limits, `max_render_width/height` (8192;
   0 = 64×64 only), `profiles` bit 3.
2. `CREATE_VM`, then `CREATE_CONTEXT{type=RENDER}`; one context per device
   and boot. Place USC/PDS code and data in `[base, base+4 GiB)` (the
   recorder's layout is fine), everything else at ≥ 1 GiB and outside the
   private window.
3. `CREATE_BO` + `VM_BIND` (or `PRIME_FD_TO_HANDLE` + `VM_BIND` for
   simpledrm dumb buffers, LINEAR, pitch = width × 4); `BO_SYNC TO_DEVICE`
   after CPU writes to non-CACHE BOs.
4. `CREATE_RENDER_TARGET` per framebuffer size, flags 0 and zero geometry:
   with `RENDER_KERNEL_GEOMETRY` any w×h up to 8192 (the compositor's
   1536×2048 scanout, 2048×1536 rotated and window-sized targets); without
   it only 64×64. Do not send USER geometry: under the new gate it must equal
   the kernel's result anyway. Create targets while idle (no job queued);
   `pm_blocks` reports the grown pool. 32 targets per context, 16 MiB
   window.
5. Per frame, `SUBMIT_RENDER_TYPED` with the pass from the recorder:
   VDM stream (+ its W88 control word), TA scratch block and its six
   pointers, the two allocator pools (V1) or the scissor/depth-bias tables
   (V2), the two context and two resume PDS descriptors, the TA state PDS
   words, background/EOT/EOT-resume/restore words; the resources list with
   READ/WRITE (IMPLICIT/CACHE for shared or scanout buffers); input
   syncobjs and one output. With `TYPED_V2`: `ext.scissor_va/count` = the
   recorder's IPF scissor table (at least one entry: when no draw scissors,
   point at a one-entry full-target table or the old POOL8 arena with 65536),
   `ext.dbias_va/count` likewise (POOL7 arena, or a table of
   {constant, slope, clamp}), `bgobj_depth` = depth clear as f32 bits (the
   captured format; with a ZLS store format other than F32 Rogue wants that
   format), `bgobj_vals` = (stencil clear & 0xff) | 0x300,
   `pixel_output_width` = 2 for one 32-bpp colour attachment,
   `background_texture` = the LOAD program's texture-state word (0 for
   CLEAR), `zls` all zero unless depth load/store is wanted (then name the
   address words in `zls_address_mask` and list the depth/stencil BOs RW).
6. Wait on the syncobj (or a sync_file), then `GET_JOB_STATUS` error 0
   before trusting pixels (an error fence also "completes"). `BO_SYNC
   TO_CPU` for non-CACHE BOs before CPU reads. No retries after an accepted
   job fails; a LOST context means device lost.
7. `VM_BIND` only with no job queued (`EBUSY` otherwise); drain the queue
   first.

## 7. Limits and trust model

- Privileged (`CAP_SYS_RAWIO`) like compute: the user PC borrows the
  firmware-GiB catalogue entry (firmware stamps/timestamps and PM objects are
  addressed through it), so GPU code in a render VM can reach firmware
  memory. Shader, PDS, VDM and PPP contents are not validated; the MMU
  (user mappings + the borrowed entries) is the only GPU-side boundary.
- The kernel never dereferences a user address; every user VA is only
  range-checked against the caller's own mappings and roles. No raw W/SKU,
  Apple record, META address, PA or register array crosses the ABI. TYPED_V2
  register images are fixed: each field has exactly one CR destination and
  one copy, chosen by the kernel.
- CPU writes to BOs used by a queued job are the caller's problem (as in
  r20/r21); LINEAR64_V1 checks exact bytes only at submission.
- Unqualified on hardware: everything. In particular repeated jobs (PM
  policy, S serial, cache repeat), idle VM edits on a live two-slot VM, RT
  geometry other than 64×64 (offline: bit-identical to Apple's arithmetic),
  pools above 32 blocks (r25 qualifies 64/128 only as latched variants),
  in-job PM growth/OOM (absent), every TYPED_V2 field (positions and CR
  offsets proven offline; ZLS order and MRT widths are hypotheses), MSAA
  (absent), preemption, reset/recovery (absent).

## 8. Mesa struct diff (round 2)

Mesa keeps its own copy of the dialect in
`src/imagination/vulkan/g5p/apple_g5p_render_typed_draft.h` (patches
0001..0015, branch `g5p-gfx`), used when the installed header lacks it.
This is the exact diff to bring it to `a24e938ae1`. The V1 layout is
unchanged; two reserved tails split; V2 is added under its own guard, so an
older installed header with V1 still compiles. With such a header (e.g.
80f73b7427: `TYPED_RENDER` present, `_V2` absent) the draft defines the V2
constants and structs but keeps the old V1 tails, so `pm_blocks` and
`max_render_*` do not exist there. Treat them as informational: read them
only when built against a header at `a24e938ae1` or later. Cap 22 implies
the 8192 limit, so correctness needs no field read.

```diff
--- a/src/imagination/vulkan/g5p/apple_g5p_render_typed_draft.h
+++ b/src/imagination/vulkan/g5p/apple_g5p_render_typed_draft.h
@@ struct drm_g5p_create_render_target {
    __u64 secondary_va;
    __u64 secondary_bytes;
    struct drm_g5p_rt_geometry geometry;
-   __u64 reserved[2];
+   __u32 pm_blocks;      /* out, RENDER_KERNEL_GEOMETRY: context PM pool blocks */
+   __u32 pad2;           /* zero */
+   __u64 reserved;       /* zero */
 };
@@ struct drm_g5p_get_render_info {
    __u32 job_timeout_ms;
    __u32 retained_results;
-   __u64 reserved[4];
+   __u32 max_render_width;   /* RENDER_KERNEL_GEOMETRY: 8192, else 0 */
+   __u32 max_render_height;
+   __u32 max_pm_blocks;      /* 128 */
+   __u32 pad;
+   __u64 reserved[2];
 };
@@
 #ifndef DRM_G5P_RENDER_ACCESS_IMPLICIT
 #define DRM_G5P_RENDER_ACCESS_IMPLICIT (1U << 2)
 #define DRM_G5P_RENDER_ACCESS_CACHE (1U << 3)
 #endif
+#ifndef DRM_G5P_CAP_TYPED_RENDER_V2
+#define DRM_G5P_CAP_RENDER_KERNEL_GEOMETRY (1ULL << 22)
+#define DRM_G5P_CAP_TYPED_RENDER_V2 (1ULL << 23)
+#define DRM_G5P_RENDER_PROFILE_TYPED_V2 3
+#define DRM_G5P_RENDER_MAX_TABLE_ENTRIES 65536
+#define DRM_G5P_RENDER_ZLS_REGS 6
+
+struct drm_g5p_render_pass_ext {
+   __u64 scissor_va;          /* ISP_SCISSOR_BASE: 8-byte IPF scissor pairs, READ */
+   __u32 scissor_count;       /* 1..65536 */
+   __u32 dbias_count;         /* 1..65536 */
+   __u64 dbias_va;            /* ISP_DBIAS_BASE: 12-byte {constant, slope, clamp}, READ */
+   __u64 background_texture;  /* PDS_BGRND1 (LOAD texture state), B-relative */
+   __u32 bgobj_depth;         /* ISP_BGOBJDEPTH: depth clear */
+   __u32 bgobj_vals;          /* ISP_BGOBJVALS: stencil [7:0] | mask [8] | bgtag [9] */
+   __u32 pixel_output_width;  /* 2, 4 or 8 */
+   __u32 zls_address_mask;    /* bit i: zls[i] is an address in a listed RW range */
+   __u64 zls[DRM_G5P_RENDER_ZLS_REGS]; /* CR f48, f50, f58, f60, f68, fd0 */
+   __u64 reserved[4];
+};
+
+struct drm_g5p_render_pass_v2 {
+   struct drm_g5p_render_pass base; /* base.size = 488; pool7_*, pool8_* zero */
+   struct drm_g5p_render_pass_ext ext;
+};
+#endif
```

Sizes and offsets (ARM32 and ARM64, `test_render_uapi_layout.c`):
`drm_g5p_render_pass_ext` 128 (scissor_va 0, scissor_count 8, dbias_count
12, dbias_va 16, background_texture 24, bgobj_depth 32, bgobj_vals 36,
pixel_output_width 40, zls_address_mask 44, zls 48, reserved 96);
`drm_g5p_render_pass_v2` 488 (ext at 360); `create_render_target.pm_blocks`
184, `reserved` 192; `get_render_info.max_render_width` 88, `reserved` 104.
Ioctl numbers and encodings are unchanged (`SUBMIT_RENDER_TYPED` stays
`0xc0506454`; the pass is pointed to, not embedded).

Changes needed in `g5p_drm_render.c`:

- **`ensure_rt()`**: keep flags 0 and zero geometry, and accept every
  size when `info.capabilities & DRM_G5P_CAP_RENDER_KERNEL_GEOMETRY`. The
  kernel derives the geometry, so do not compute R-values in the ICD.
  Refuse sizes above `max_render_width/height` before the ioctl. Create
  targets at transport setup or between jobs (an `EBUSY` while a job is
  queued is a programming error with the synchronous queue).
- **`fill_typed_pass()`**: with `DRM_G5P_CAP_TYPED_RENDER_V2`, fill a
  `drm_g5p_render_pass_v2` (`base` as today but `pool7_*` and `pool8_*`
  zero, `base.size = sizeof(v2)`), `ext` as in section 6 step 5, and
  submit `profile = DRM_G5P_RENDER_PROFILE_TYPED_V2`,
  `pass_size = sizeof(v2)`. Also list the scissor/dbias table BOs (READ)
  in `resources` and any ZLS BOs (READ|WRITE).
- **`typed_submit()`**: lift the `j->scissor_count` refusal under V2
  (`scissor_va = j->scissor_table_va`, `count = j->scissor_count`), and the
  `j->depth.present` refusal for on-chip depth (no load/store:
  `bgobj_depth` = f32 bits of `j->depth.clear`, `bgobj_vals =
  (j->depth.stencil_clear & 0xff) | 0x300`, ZLS zero). LOAD_OP_LOAD and depth load/store stay refused until
  the background-load program and the ZLS words have capture evidence
  (round17 report section 4).

Device ladder and offline evidence: experiments/render-user.md (`experiments/render-user.md`, not published).
