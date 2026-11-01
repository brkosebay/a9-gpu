# Mesa on A9 G5P — decision and implementation plan, 2026-10-22

**Round-5 update:** a separate opt-in experimental pvr/G5P ICD now enumerates,
compiles simple shaders and records CDM. An internal render builder on the
mock winsys records VDM/PDS/PPP for a bounded triangle, with explicit missing
PBE/firmware state. This is not a conformant Vulkan device, a wired Vulkan
render path or a real DRM execution adapter. See round5-report.md (`round5-report.md`, not published)
and the full pinned GLES30 gap list (`zink-gles30-gaps.md`, not published). Architectural
milestones below remain the production plan; initial loader/compiler/recording
seams are now implemented experimentally.

**Verified round-4 progress:** round4-report.md (`round4-report.md`, not published) adds a
real SPIR-V → Mesa NIR → external SSA/G5P compiler (329/1,071 corpus
equivalents, 16/16 conversion probes, 33/39 explicit GLES-style cases),
compute byte emission and a C1–C3 mock. The two-shader PCO experiment below
remains a separate regression. General PCO allocation/scheduling and a
working Mesa ICD/winsys remain future integration work. The separate
`apple_g5p` UAPI proposal is reviewed in uapi-needs.md (`uapi-needs.md`, not published).

## Decision

**Inferred engineering decision:** build a project fork of Mesa's **Imagination
pvr Vulkan stack with a G5P compiler/encoding path, then Zink for EGL/GLES**.
Use the existing PCO IR as the first backend integration point; keep early
Linux bring-up in small independent compute/render programs. Do not start
a second, native Gallium hardware driver this round.

**Verified reasons:** measured BVNC is 9.15.6.2, absent from the pinned
Mesa/Linux supported-device tables. The captured PDS, CDM and VDM packets
match Rogue formats in the tested subset, whereas USC encoding does not.
Mesa already implements the tiler/fragment job split, resource management,
shader infrastructure and a Vulkan API frontend. This round built the real
PCO compiler and consumed its IR with a working external G5P encoder.
See [ISA spec](isa-g5p.md), [streams](command-stream.md) and
[kernel identity](../gpu/core-configuration.md).

**Inferred tradeoff:** Vulkan is more API work before GL than a purpose-built
Gallium driver, but a Gallium driver would also require new screen/context,
state tracking and draw emission around a still unverified GPU. Reusing
pvr offers the strongest starting point for the measured Rogue render
structure. Revisit the decision after first textured rendering if G5P
cannot satisfy Zink's GLES 3.0 profile, or pvr's job/resource assumptions
prove more costly than a thin Gallium frontend. Keep encoders and packet
builders frontend-independent so that pivot does not discard the RE.

**Verified qualification:** Zink is Gallium-on-Vulkan, and its GL/GLES levels
depend on Vulkan capabilities. The GLES 3.0 gate is the actual
`VP_ZINK_gles30_baseline` profile and its dependencies in
`src/gallium/drivers/zink/VP_ZINK_requirements.json`, plus conformance tests;
a triangle is not evidence of GLES 3.0. The official
[Zink documentation](https://docs.mesa3d.org/drivers/zink.html) describes the
profile check. The [PowerVR documentation](https://docs.mesa3d.org/drivers/powervr.html)
does not list G5P as supported. No existing pvr+Zink success on this A9 is
asserted here.

## Architecture and what is reused

**Inferred target flow:** Hyprland/aquamarine → EGL + GBM/DRI → Gallium Zink
→ Vulkan loader → pvr with G5P paths → DRM render node → Apple's META
firmware → PDS/USC/TA/3D. Rendering produces dma-bufs for the display/KMS
path; display and render devices can remain separate. The simpledrm/llvmpipe
desktop is the integration baseline, supplied by the orchestrator.

**Verified source baseline:** Mesa
`566ed9cd9dff0a0825c7ea71fc3c31a4af03d5a7` (`27.0.0-devel`), checked out
unmodified under `third_party/mesa-g5p`. This snapshot has **PCO**, not an
active `src/imagination/rogue` directory. Earlier Mesa had both compilers;
“Rogue” here names the architecture, not a requirement to revive the old
compiler. Relative paths below refer to that exact snapshot.

| Component | Verified source/evidence | Inferred implementation decision |
|---|---|---|
| NIR, SPIR-V frontend, util, Vulkan runtime | `src/compiler/`, `src/vulkan/runtime/` | Reuse infrastructure as-is; add G5P compiler options/lowerings. |
| EGL, GBM, Gallium state tracker, Zink | Existing Mesa frontends | Reuse as-is after loader/device matching, external memory and required Vulkan features work. They do not themselves create GPU support. |
| PCO IR/builders, printing and ending pass | `src/imagination/pco/`; exercised this round | Reuse IR/storage and selected early optimizations. New G5P target pipeline; do not call stock `pco_process_ir` unchanged. |
| PCO register allocation, control-flow/boolean lowering, scheduler, grouping | `pco_ra.c`, `pco_cf.c`, `pco_bool.c`, `pco_schedule.c`, `pco_group_instrs.c` assume Rogue banks/phases | Audit and adapt separately. Initially use a small G5P-specific allocator/scheduler if that avoids unverified assumptions. Grouping and `pco_binary.c` cannot emit G5P. |
| PCO NIR texture/memory/output lowering and USC library | Generated helper library built successfully | Port ABI, descriptors, memory/address and pixel output conventions; helper programs contain stock Rogue instructions and are not executable G5P blobs. |
| `pdsc` PDS ISA/compiler | Captured DOUT_EXT layout matches `pdsc_dout_pack` | Reuse field packing/opcodes for measured forms; configure DOUT_EXT and port G5P launch/DMA/context programs. Do not accidentally use legacy non-EXT DOUT headers. |
| `csbgen` and Rogue XML | CDM/VDM measured subset matches | Reuse generator and tested pack definitions; extend Apple CDM type 3 and resolve KERNEL0 sizes, PPP/ISP/PBE/ZLS. Revalidate each used field on G5P. |
| pvr resource, descriptor, render-pass and command infrastructure | `src/imagination/vulkan/` | Reuse architecture, adapt layouts and state emission. Descriptor and render-target compatibility are not established by CDM/VDM matches. |
| pvr device info and errata | `common/pvr_device_info.c`, `common/device_info/` | Add measured 9.15.6.2 entry. Query/RE limits, caches, tile geometry, compression and BRNs; never alias GX6650. |
| pvr winsys abstraction | `vulkan/winsys/pvr_winsys*.{c,h}` | Reuse abstraction. Stock powervr implementation only if the kernel implements all its semantics; otherwise add an Apple/G5P implementation behind it. |

**Verified compiler pipeline constraint:** `pco_process_ir` runs optimization,
control-flow lowering, scheduling, register allocation, task ending and
Rogue grouping in one function; `pco_encode_ir` consumes grouped IR. A
G5P backend needs a separate pipeline/dispatch before incompatible grouping,
not merely replacement bytes at the end of `pco_binary.c`.

**Inferred code to write:** target feature/ABI data; G5P legalizer, instruction
selection, encoding and branch fixups; bank-aware allocation and hazard
scheduling; memory and texture descriptors; shader metadata (temps,
shared/coefficient sizes, outputs); vertex fetch, iterator and pixel output
programs; background/clear/end-of-tile/fence code; CDM/VDM/PPP/ISP/PBE
emission; heap allocator/base handling; winsys jobs and synchronization;
dma-buf layout/modifiers and display integration. This is a driver project,
not a two-shader encoder patch.

## Kernel contract — matched to the available kernel notes

**Verified documentation state:** the kernel agent's new
[drm-design.md](../gpu/drm-design.md) proposes a **separate Apple G5P render-only
DRM backend**, with 16 KiB GPU pages, GEM/VM/BO objects, versioned submission
and syncobj points; simpledrm retains scanout initially. Its
[submission-g5p.md](../gpu/submission-g5p.md) recovers the CL work-object chain,
separate pointer/notification rings, stamps and microsequences. G2 and real
compute remain unvalidated on device; no production submission UAPI exists.
The newly implemented dependency-as-NOP probe tests queue transport only.
A 20-byte notification ring or a 44-byte event ring is not a compute job.

**Inferred matched decision:** use **a G5P Mesa winsys behind pvr's existing
winsys abstraction** for the proposed separate kernel driver. Reuse DRM core
and selected powervr BO/VM/scheduler patterns as the kernel design proposes.
Keep `pvr_drm.h` compatibility an optional later optimization, requiring a
field-by-field semantic contract. Existing IMG FWIF init/queues cannot boot
or submit to Apple's firmware unchanged. Admitting BVNC 9.15.6.2 through a
whitelist does not establish compatibility.

**Verified required powervr concepts:** `include/drm-uapi/pvr_drm.h` and
`vulkan/winsys/powervr/` use device/runtime, quirk/enhancement, heap and
static-data queries; BO create/mmap/destroy; VM/context lifecycle and fixed-VA
mapping; free-list/HWRT objects; and `SUBMIT_JOBS` with synchronization.
The winsys validates device info and required heaps/static data, not just
ioctl numbers. Driver selection also checks DRM driver name; a new
`apple_g5p` name needs explicit loader/winsys matching.

**Inferred concrete contract for kernel review:**

1. Render-node GEM BOs, CPU mapping, 16 KiB host-page compatibility and an
   explicit coherency contract. Report GPU page size, alignment, VA range,
   firmware reservations and mapping lifetime. Refuse out-of-range mappings;
   a sync fence must imply visibility of completed GPU writes.
2. User-chosen VAs in a per-process VM. The first replay uses original VAs,
   then relocation changes one pointer class at a time. Submission pins
   referenced BOs and defines unmap/destruction behavior for in-flight jobs.
3. A measured **common relative base B for USC and PDS**, including data references.
   Mesa's usual separate USC and PDS logical heaps must be backed by
   compatible subranges and encode offsets relative to B, or the winsys and
   code emission must adapt. Distinct logical heap bases cannot silently be
   subtracted from addresses captured relative to one common base. Program
   the base registers/context fields using the recovered Apple mechanism.
   **Verified new kernel stores:** its GART range is `0x300000000` with 4 GiB
   length; two META-init fields +0x10/+0x18 receive `0x300000000`.
   **Inferred:** that is the leading absolute B candidate. Firmware reads
   and final execution-base MMIO writes are still unresolved; do not relocate
   a capture to that B as though it were device-verified.
4. Compute context/job: CDM base and terminate-word VAs, scratch/context
   objects, code base and resources. Translate a versioned userspace job
   description to AGFI, with accepted fields defined. First expose a bounded
   private diagnostic submission interface if needed; do not freeze that as
   the desktop UAPI.
   **Verified new CL chain:** payload C+0 maps to work W+0x70 (CDM base),
   C+0x60 to W+0x23c (terminate VA); C+0x58 maps to W+0x60 and is scratch,
   not terminate. The kernel must create the remaining context, dependency,
   stamp and microsequence objects. A consumed work notification alone is
   insufficient completion evidence.
5. Geometry and fragment jobs: VDM stream, parameter buffer/free lists,
   HWRT/region headers, ISP/PBE/ZLS/clear/EOT state and TA→3D dependencies.
   AGFI object ownership/grow/OOM behavior must be recovered. These cannot
   be inferred from userspace stream captures alone.
6. Binary/timeline syncobjs as required, dma-fence dependencies, timeout,
   reset/fault reporting and a subsequent good-job test. PRIME dma-buf
   import/export, modifiers/strides and external semaphore/fence support
   are desktop gates, not assumptions supplied by Vulkan runtime code.
7. Regenerate required static USC/PDS helper areas in G5P encoding. Stock
   Rogue precompiled EOT/fence/VDM-sync programs are not usable G5P shaders.

**Inferred primary integration:** keep pvr's API/command layer and implement
the same `pvr_winsys_ops` contracts against the G5P-specific UAPI
for BO/VM/context/job/free-list/HWRT objects. A new winsys is a bounded
adaptation compared with implementing another graphics API frontend.
Do not ask the kernel to accept opaque userspace AGFI blobs as a final
unvalidated submission ABI.

## EGL/GLES and display acceptance

**Inferred staged delivery:** first headless Vulkan compute/render. Then
Zink's `VP_ZINK_gles20_baseline`, followed by `gles30_baseline`; GLES 3.1/3.2
are later feature gates. Validate the exact JSON profile at the pinned Mesa
revision rather than transplanting desktop GL requirements into GLES.
Tests must include textures/samplers, UBOs/SSBOs as required, precision,
integer operations, loops, blending, depth/stencil, scissor, MSAA/resolve,
render-to-texture, synchronization, robustness and format/limit reporting.
Software emulation in NIR is acceptable where it meets the required semantics.

**Inferred desktop gate:** create an EGL GBM context on the render node;
verify EGL dma-buf import/export, surface/config formats and the extensions
used by the installed Hyprland/aquamarine build. Present a linear BGRA/XRGB
buffer through the existing KMS device. Test whether simpledrm can import
that dma-buf with its actual format/modifier/stride; if not, use an explicit
copy into a display-owned buffer or add display-driver support. Do not
replace the functioning display stack on the assumption it needs a new GPU.
A CPU scanout copy is a useful interim display path, not proof of zero-copy.

**Inferred test configuration once a driver exists:** build Mesa with
`-Dvulkan-drivers=imagination -Dgallium-drivers=zink -Degl=enabled -Dgbm=enabled`
plus the target's Wayland platforms. Select that ICD with `VK_DRIVER_FILES`
and Zink with `MESA_LOADER_DRIVER_OVERRIDE=zink`/`GALLIUM_DRIVER=zink` as
needed. Confirm Vulkan device identity, EGL vendor/renderer and GPU job
activity; do not use GL/GLES version overrides to masquerade as support.
The offline macOS build below intentionally does not build that desktop stack.

## Milestones, gates and effort

All durations below are **inferred estimates**, for an experienced graphics
engineer with regular device capture/test windows. They are not deadlines.
Kernel progress is an independent dependency and can dominate calendar time.

| Milestone | Concrete gate | Work estimate |
|---|---|---|
| M0 — offline encoders/compiler seam | This round: built PCO; exact VS/FS bytes and unknown-op rejection | Complete for the narrow prototype |
| M1 — first Linux compute | C1 replay, C2 own store constant, C3 own PDS/CDM; two constants, untouched sentinels, clean completion and next job | 1–3 weeks **after** working firmware, VM, B and COMPUTE submission exist |
| M2 — first own triangle | R1–R4 in [first-triangle.md](first-triangle.md); 64×64 readback matches iOS, own VS/FS/PDS/VDM/PPP, recoverable failure | 4–10 weeks after TA/3D/parameter-buffer contract exists |
| M3 — general shader/texture subset | NIR→G5P without shader-name special cases; textured/blended render-to-texture, loops, uniform/buffer access; targeted Vulkan tests | 2–4 engineer-months |
| M4 — EGL/GLES 3.0 through Zink | Correct profile/limits, GBM EGL context, targeted GLES CTS/dEQP, dma-buf/fences; no capability overrides | 3–6 engineer-months; conditional on hardware capabilities and pvr gaps |
| M5 — Hyprland on GPU | Compositor effects, window damage/copy, client texture import and synchronization, working presentation, stable suspend/recovery plan | 2–4 engineer-months |

**Inferred total:** roughly **9–18 engineer-months of userspace and
integration** for a usable, limited GLES 3.x desktop; potentially longer for
conformance/performance/reliability. Kernel firmware/VM/TA/3D RE is additional
and currently has unbounded compatibility risk. Two complementary specialists
with device access could overlap work; no credible calendar promise follows
while G2 is open. A basic accelerated demo can arrive much earlier than a
stable compositor. The byte-exact prototype proves the compiler seam, not
that the remaining ISA/driver work is a few weeks.

## Built prototype and reproduction

**Verified:** `third_party/mesa-g5p` is unmodified at the pin above. Release,
no debug info, native macOS ARM64 build produced:

- `out/src/imagination/pco/libpowervr_compiler.a` (full PCO source list,
  including NIR lowering, allocation, grouping and binary encoder);
- `out/src/imagination/common/libpowervr_common.a`;
- `out/src/imagination/pco/uscgen/pco_clc`, and generated USC library bindings.

**Verified build detail:** `-Dtools=imagination` alone reaches a missing
`prog_mesa_clc` in this revision. Enabling LLVM 19 and
`-Dmesa-clc=enabled -Dinstall-mesa-clc=true` supplies the actual helper compiler.
SPIRV-LLVM-Translator was built privately under `third_party/`, not replaced
with stubs. `mesa-prototype.sh` pins Mesa, translator and SPIR-V headers;
Python packages are in `third_party/g5p-venv`; the host's existing LLVM 19,
SPIRV-Tools, CMake/Ninja, pkg-config and Xcode are prerequisites. Mesa's
compile database and `pco_clc` link command are reused for ABI-correct linkage.
Keep `NDEBUG` identical: PCO internal struct layout changes with it.

**Verified external backend:** `tools/gpu-userspace/g5p_pco_proto.c` builds
PCO `MOVI32` and `UVSW_WRITE` instructions using real builders and physical
register references, invokes the actual `pco_end` pass, traverses the resulting
PCO IR, and emits G5P parcels with a separate C implementation. Unknown IR,
conditional execution and unsupported banks are rejected. PCO_END combines
the last UVSW with endtask; the G5P encoder adds the measured 64-byte vertex
split or fragment pixel emit/terminator. Input words are already packed
according to the characterized fragment ABI.

**Verified checks:** `check-pco-proto.py` reports **133 checks, zero failures**:
four exact host/device reference comparisons, 128 independent Python-versus-C
immediate/output-program encodings, and rejection of unsupported IR. VS is
70 bytes, solid-red FS 32 bytes. Reference SHA256:

- VS: `340c84a9e028dd79f65ef7ff37d4d9082100f4f403ee5764b6b494dc1d399978`
- FS: `a04f33c8f088924457f416f50815dadcbac34cc6833666944363c33cb6fa82bf`

**Verified limits:** shader IR is constructed explicitly. The NIR objects
carry stage/options, not a translated application. No general NIR→PCO→G5P,
PCO register allocator/scheduler, metadata ABI, Linux submission, pixel image
or performance has been tested. These are not hardcoded reference byte
arrays; encoding is operand-driven, with task-layout conventions limited
to the two characterized shader forms.

**Verified reproduction** from repository root:

```sh
bash tools/gpu-userspace/mesa-prototype.sh
# Fast relink/recheck, after the compiler build:
python3 -B tools/gpu-userspace/build-pco-proto.py third_party/mesa-g5p
python3 -B tools/gpu-userspace/check-pco-proto.py third_party/mesa-g5p \
  build/gpu-userspace/offline/split build/gpu-userspace/device-analysis/split-air
```

**Verified artifacts:** `third_party/mesa-g5p/out/g5p-prototype/` contains
`g5p-pco-proto`, `vs.bin`, `fs.bin`, `checks.json` and the PCO IR logs. Build
logs are `third_party/g5p-meson-setup.log`, `g5p-mesa-build.log` and
`g5p-prototype-reproduce.log`. The checkout plus build and private dependency
outputs occupy about 1 GiB, with debug info off. Apple reference bytes remain
in existing ignored `build/`; new generated bytes are in ignored `third_party/`.
No kernel tree, kernel docs, device state or workspace commits were changed.

**Verified policy:** the checkout's `AGENTS.md` prohibits modifications to
`src/imagination`, whose README declines LLM-generated contributions. The
compiler and backend experiment above leaves those sources untouched and
lives in this project's tools. No upstream submission is part of this task.
Any later upstream-targeted work needs human authorship/review consistent
with that policy; a project fork is the integration target considered here.
