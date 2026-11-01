# G9P → G5P port: what we take from Henrik's A10 Mesa driver

Date: 2026-11-01. Offline lane: no device access, no workspace commit. Owner direction: "Take everything we can
from Hendrik's A10 Mesa driver. No duplicate code: port his into ours."

- **His code:** `OrchardLinux/mesa` branch `main` @ `fece4659a8d`. It is 36 commits by Henrik Klee
  `` on upstream Mesa `801952de2f1` (2026-10-29), all MIT-licensed and all carrying
  `Generated-by: LLM`. Fetched as a blobless bare clone (`build/third_party/orchard-mesa.git`) plus an export of
  `src/imagination` (`build/third_party/orchard-mesa-src`). It was read as data only.
- **Ours:** `third_party/mesa-a9-prod`, branch `g5p-wsi@32b83afe10c` (the desktop ICD `ac0460f9`) and the lane
  branches named below.
- **Not in our repo:** `g5p-textypes` doesn't exist yet. Its row in the risk columns is "unknown".

## 0. Summary

**Compiler verdict: graft, don't replace.** Swapping in `g9p_compile.c` for our pco G5P backend would lose
things that are proven on our hardware, and his compiler has no equivalent for them (§2). We take three pieces
of his compiler, each as its own commit.

**Top 5 port items** (value × safety, in port order):

| # | Item | His commit | Decision | Status (Phase 2) |
|---|---|---|---|---|
| 1 | Discards in blending shaders become a select on transparent black (`discards_to_select`, "zero" form) | `7013b551398` + `7a64e1d1bf4` | **ported**, on by structure: only programs that blend (premultiplied or straight-over) and discard | `b3eddec4eab`. Changes only blend ∧ discard programs. Of the 32 scenes, only `rounded-border` changes (Hyprland's border shader; never flown, outside labwc's evidence) |
| 2 | FMA fusion | `28cdfceab8e` (+ `906f3eab2a6`'s lesson) | **ported**, fragment only, behind `PVR_G5P_FUSE=ffma` (fragment key bit `fuse_ffma`), and only with last-read groups | `2738d4c046e`. Off by default: every proven program keeps its bytes. With it on, round 19 `math.frag`, `alu.frag`, `ubo.frag` and `rounded.frag` shrink (by 2–24 bytes). **The production trampoline nulls `PVR_G5P_FUSE`**, so trying it on the desktop needs a `P_PASS` entry there (§6) |
| 3 | Sampler edge-case test (coordinates past 1, clamp vs repeat, nearest and linear, NPOT and non-64-byte widths) | `3ca781076f4` (the test; his bit fix is already in ours) | **already ported by `g5p-gles3-tex`**: its device test T2, "Hendrik's edge test", passed on hardware (`gles3-tex.md` §5) | nothing to add (no duplicate) |
| 4 | Registers above r31 ("homes") for fragment shaders that run out | `d1ab45daed3` | **later, after an A9 check**: A9 has never run a register above r31, and its encoding is not compared yet | encoding **compared: identical** (Apple's A9 r32..r69 = his extension word; encoder ported, `g5p-cf@05111be059f`); allocation not wired (`control-flow.md`) |
| 5 | Control flow (if/else/loops with a mask stack, discards in control flow as a flag) | `d91427625ff`, `d1ab45daed3` (`discards_to_flag`) | **later**: the largest functional gain for Hyprland, and the largest risk | **ported** on `g5p-cf@4879eea4be3` (A9 encodings = his, byte-checked; device ladder pending): `control-flow.md` |

**Not ports** (we already have it, it doesn't apply, or it isn't Mesa code on A9). Of the task's "likely first
picks", only discard-as-select and FMA fusion were real ports. The gles3-tex lane had already ported the sampler
test:

- **Per-render compile skip:** doesn't apply. `g5p_CmdBeginRendering` (`vulkan/g5p/g5p_cmd_gfx.c:52`) emulates
  `vk_render_pass` and compiles nothing. Shaders compile only at pipeline creation
  (`g5p_pipeline.c:377,379` → `g5p_compile_graphics`). Upstream's SPM/load-op path (`pvr_arch_framebuffer.c`,
  `pvr_arch_mrt.c`, which his `b9721ab4b49` bypasses) is not linked into our ICD's render path. To find our real
  per-frame CPU cost, profile first.
- **Sampler bit fix:** already ours, and more complete. See §1, row "Samplers".
- **Native premultiplied blending:** already ours. `g5p_blend_epilogue()` (`pco/g5p_recipes.c:174-196`) is
  Apple's A9 premultiplied tail (the tile read into halves 4..7, `1 - srcA` in half 12, four half FMAs with
  per-lane tile unpacks), and labwc runs it on hardware (`BLEND` evidence). His tail (`g9p_blend_src_over`, 1 −
  alpha in half 10, no per-lane unpack) is Apple's A10 output and has a different length.
- **Tile skipping:** not portable to the ICD. On A9 the kernel builds ISP_CTL (W+0x3c0 → CR 0xf38) as Apple's
  `0x0021000f`, whose bit 17 (PROCESS_EMPTY_TILES) is already clear. The firmware sets bits 18:17 itself (RMW at
  `1001c520`, `dsx-nx13-isp-gate.md` §4), which is why "every tile runs the background" on A9
  (`partial-redraw.md` §2, §5). The per-draw region clip, the ICD half of his change, is already ours
  (`g5p_record.c:1076-1085`, the clip is the draw's scissor ∩ render area). What's left is a kernel/firmware RE
  item: what makes the A9 firmware set PROCESS_EMPTY_TILES.
- **Background load and clear:** ours is Apple's own A9 LOAD object, captured on iOS and proven on hardware
  (`g5p-partial`). Keep ours.

## 1. Feature map

Proven = verified on A9 hardware. Lanes: wsi = `g5p-wsi`, arena = `g5p-arena-fix`, depth = `g5p-depth`, tex =
`g5p-gles3-tex` (stacked on `g5p-mrt`), vpds = `g5p-vpds`, xfb = `g5p-xfb-fix`, mrt = `g5p-mrt`, partial =
`g5p-partial`, vary = `g5p-varyings` (today the same commit as tex, `9bbd475d53a`).

| Feature | Ours (G5P) | His (G9P) | Ours proven? | Decision | Dependencies | Conflict risk with active lanes |
|---|---|---|---|---|---|---|
| **Compiler structure** | pco backend: `pco/pco_g5p_general.c` (NIR→USC, straight-line), `g5p_usc.c` (field encoders), `g5p_recipes.c` (Apple's lowerings), `pco_g5p.c` (fixed shapes); NIR front end in `vulkan/g5p/g5p_nir.c`; JSON refusal and evidence output that the shader gates read | `g9p/g9p_compile.c` (NIR→G9P, own allocator, control flow, compute) + `g9p_isa.h` encoders; output `struct g9p_shader` for upstream `pvr_*` pipelines | yes: the labwc desktop, XFB, MRT, depth, LOAD, the GLES3 textures | **keep ours; graft** (§2) | - | the compiler files are edited by depth, tex, mrt, vpds, xfb and vary: every graft must be a small, self-contained hunk |
| **ISA encoders** | `g5p_usc.c`: the round-18/19 field encoders, checked against Apple's corpus | `g9p_isa.h`: inline word emitters | yes | **keep ours.** The words are identical on every form compared (a9-reply §1.1). His encoders only fill gaps where we lack a form: high registers (`g9p_mov_to/from_high`), the computed-coordinate sample (`b8c3dec72cf`) | - | low (new encoders are additions) |
| **Register allocation** | 32 dwords (`NUM_DWORDS`), first-fit with Apple's measured choices (a dying operand in source B / FMA A, its register reused); on pressure, one retry with constants re-read per use (`remat`), then a refusal | 30 registers (`G9P_REGS`), a mask stack above them, two passes (count, then emit) | yes | keep ours | - | high if touched (vpds edits `alloc_run`; depth, tex and vary edit `liveness`) |
| **Spilling / registers above r31** | none: refuses ("register pressure") | fragment homes r32..r127: a source operand with bit 0 set plus an extension word, `OR` moves; NIR register with the furthest last use parked, generation restarted (`d1ab45daed3`) | n/a; **A9 has never executed a register above r31** | **later, behind an A9 check** (top-5 #4): compare the extension-word form with Apple's A9 corpus first, and fly a fixture | the encoder pair; a refusal log showing a real pressure failure | medium: the fallback lives in `codegen`/`two_pass` (vpds edits `codegen`) |
| **FMA** | `emit_ffma` (fragment) and `vs_ffma` (only the measured vertex forms: f16-literal header 0x19); NIR options `has_ffma` without `fuse` (`g5p_nir.c:117`), so NIR never fuses `fmul`+`fadd` | `has_ffma \| fuse` (`28cdfceab8e`); his addend kill-bit fix (`906f3eab2a6`) | a register `ffma` in a fragment program has run on hardware only in labwc111's litblend **fixture** (its R channel), not in labwc's own shaders | **ported** (top-5 #2), fragment only, behind `PVR_G5P_FUSE=ffma`, and only with last-read groups. NIR's fusion rule is in `nir_opt_algebraic_late` (fadd(contract) of an fmul that only fadds read), which our backend doesn't run, so turning on `fuse` in the options alone does nothing (tried). The port is that single rule as a pass after `lower()`. His kill-bit bug (`906f3eab2a6`) is the reason for the last-read requirement. `emit_ffma` takes the C operand's group from `vgrp()`: with last-read groups it is 1 only on the last read, but without them every read gets group 1, the same bug class. An offline byte check covers it (§3.1) | - | low: new static functions plus one call in `pco_g5p_compile_general_key`; one key bit appended to `pco_g5p_fs_key` |
| **Last-read source groups** | `two_pass`: a count pass records each register's last read, the emit pass sets Apple's group bits (`PVR_G5P_GROUPS=last-read`, required in the desktop session) | none: `g9p_srcs(…, kill)` is almost always false | yes (the labwc blend fix) | keep ours | - | - |
| **Blending** | blend key compiled into the program: replace, premultiplied, straight-over (premultiplied in f32 first), then Apple's A9 epilogue | `7a64e1d1bf4`: native premultiplied tail; other modes through `nir_lower_blend` in f32 | yes (labwc `BLEND`) | **keep ours.** Other blend factors: his `nir_lower_blend` route is the generic fallback we refuse today; it needs a tile read in f32 (his note: about 40 instructions per pixel). Not now | - | - |
| **Discard in blending shaders** | hardware discard after the body (`fs_discards`: test + `04 61`/`04 68`), also when the program blends | `7013b551398`: discards become a select; with native source-over, select transparent black (`7a64e1d1bf4`), which leaves the tile unchanged | the hardware discard is flown only without blending (FAST_PUNCH_THROUGH, `gles3b`); **blend ∧ discard has never run on A9** | **ported** (top-5 #1), as a NIR pass after `lower()` for PREMULT/STRAIGHT_OVER with discards. It handles colour stores to `DATA0` only; after the merge with `g5p-mrt`, an MRT program that blends and discards keeps its hardware discard (conservative) | the trampoline's TRANSLUCENT rule (dst-reading jobs) is exactly the pass his bug lives in | low: `apply_fs_key` is untouched by every lane |
| **Discards in control flow** | `flatten()` turns ifs into selects (`discard_ok`), loops are unrolled, anything left is refused | `discards_to_if`, `discards_to_flag` (`d1ab45daed3`) | n/a | later, with control flow (top-5 #5) | control flow | medium |
| **Control flow** | none on the GPU: peephole select + loop unroll, the rest refused | if/else, loops, break, mask stack (`d91427625ff`) | n/a | **later** (top-5 #5): the biggest gain for Hyprland's shaders; needs its own A9 ISA check (branch and mask forms) and device fixtures | - | high (`scan_cf`, `codegen`) |
| **Transcendentals, step/lerp** | `g5p_recipes.c`: Apple's measured lowerings (rcp, rsq, log, exp, sin, cos, …) | `8d3796eb233` | partly (labwc doesn't need them) | keep ours (Apple's measured A9 recipes) | - | - |
| **Texture sampling** | coordinate from a varying (flown); computed coordinates (`tex_computed`) built, not flown, and listed as an unflown form in the wsi shader gate | computed coordinates proven on A10 (`b8c3dec72cf`) | varying: yes; computed: no | **cross-check the words** with his encoder (the texture-sample words are on the "not compared" list) before flying ours. No code port | - | low |
| **Samplers** | `g5p_sampler_encode` (`g5p_mem.c:494-560`): Apple's `SamplerStateEncoderGen2` layout. mag 23, min 25, mip 27/28, 3-bit address modes at 29/32/35 (V = dword 1 bit 0), clamp-to-zero, mirrored repeat, LOD range | `3ca781076f4`: the corrected 1-bit layout (min 25, U repeat 29, V repeat dword 1 bit 0) | yes (tex: T0–T4 PASS) | **keep ours.** His test is already ported: tex's T2 (past 1.0, NPOT 5×3, 20-byte rows, nearest and linear) passed on hardware | tex edits `g5p_sampler_encode` (hunks at 524/544: anisotropy) | none |
| **Texture descriptors** | 8 dwords + the stride rule (64-byte rows, `G5P_LINEAR_STRIDE_ALIGN`) | same layout; twiddled as well | yes | keep ours | - | - |
| **R/B swap for swizzled views** | tex `942f10cedf3` composes the view swizzle into the selectors | `2f09119a9bc` | yes (tex) | keep ours | - | - |
| **Background load / clear** | Apple's A9 LOAD object (captured `bgrnd0 = 0x28c0/0x2840`), clear background, partial render areas | own load program, `47c5ed2f74b` | yes (partial) | keep ours | - | - |
| **Tile skipping** | the region clip is already per draw | region clip per draw + ISP_CTL bit 17 off for LOAD / don't-care (`42f87aff113`, `b15b5418b76`) | n/a | **not portable** (kernel/firmware, §0) | kernel RE of the firmware RMW `1001c520` | - |
| **Render-area tracing** | `PVR_G5P_TRACE_DIR` / `PVR_G5P_TRACE_SUBMIT`: full job dumps with evidence | `G9P_TRACE` line per render (area, tiles, load op) | yes (offline tooling) | keep ours; a one-line area/tiles summary is optional | - | low |
| **Per-render SPM/load-op compile** | none (§0) | skipped for G9P (`b9721ab4b49`) | - | **N/A** | - | - |
| **Pass type** | the ICD records OPAQUE (`g5p_record.c:472`); the Mesa117 trampoline sets TRANSLUCENT for destination-reading jobs (labwc114/115/122) | translucent for fbfetch/blend, punch-through (4) for discard (`emit_draw`) | yes (trampoline) | keep ours. Note: depth adds passtype 4 for discard in the ICD (its `g5p_record.c:1281`). Moving the rule into the ICD is that lane's job; watch for double application with the trampoline | trampoline pin (`G5P117_ICD_SHA256`) | depth |
| **12-temp vertex limit** | refusal at pipeline creation (`g5p-wsi-fix@4585835ac8c`, not in `g5p-wsi`) | VS register count in VDM_STATE5; no such limit known on A10 | yes (W3 wedge) | keep ours; carry `4585835ac8c` when this branch merges with wsi-fix | - | wsi-fix |
| **XFB (4-byte vertex stores)** | xfb lanes (`g5p_xfb.c`, `st` forms) | none ("transform feedback … not done") | yes | keep ours | - | - |
| **MRT end-of-tile** | mrt (`g5p_mrt_out.h`, `g5p_mrt_programs.h`) | none ("MRT … not done") | yes | keep ours | - | - |
| **Compute** | `g5p_compute.c` + pco | `g9p_cdm.h`, `pvr_arch_g9p.c` (CDM, dispatch) | yes (r24 compute) | keep ours (a different firmware/UAPI path) | - | - |
| **Twiddled CPU transfers** | linear only | `89b19a3b464` | - | N/A (no twiddled images on our path) | - | - |
| **32 MiB parameter buffer, render ordering, G9P_TIME, PM dumps** | the kernel's job | `4ac3eedab44`, `8433037fe15`, `483a9a3f5a6`, `1ed725739bf` | - | N/A for Mesa (A9 kernel/firmware) | - | - |
| **Draw kept whole across control-stream buffers** | our recorder writes into our own arenas (`g5p_record.c`) | `06f246344a6` (upstream `pvr_csb`) | yes | N/A (a different recorder) | - | - |
| **Hyprland/Zink specifics** | Zink: dynamic rendering by capability, extended dynamic state, multi-command-buffer submits, WSI | Zink runs on upstream `pvr` (`cd3abe91d03`: transfers, UBOs, push descriptors); Hyprland shader forms (`13cd33f933a`) | Zink/labwc: yes | take his forms as test inputs: Hyprland's shaders are the corpus for control flow, high registers and blend ∧ discard | - | - |

## 2. Can we replace our compiler with his?

**No.** Each item below is proven on our hardware, and his compiler has no equivalent for it:

1. **XFB 4-byte stores.** The 16-byte store form is dropped in vertex tasks on A9. He has no transform
   feedback at all.
2. **MRT end-of-tile.** Apple's multi-target emitpix and contiguous output registers (mrt). He has no MRT.
3. **Apple's last-read source groups.** The desktop needs `PVR_G5P_GROUPS=last-read`. Without it, blended
   draws lost the destination term on A9 (labwc113/114). His operands carry no kill bits apart from one
   explicit case, and his `906f3eab2a6` shows that a wrong kill bit breaks pixels.
4. **Pass-type rules.** These are driver-side (the trampoline, later depth), so they aren't a compiler matter.
   But his `uses_fbfetch`/`uses_discard` → pass mapping differs from Apple's A9 contract (labwc114 §1:
   TRANSLUCENT only when the colour math consumes the destination; PUNCH_THROUGH vs FAST_PUNCH_THROUGH).
5. **The 12-temp TA limit.** His vertex path has no temp budget. Vertex shaders above 12 temps wedge the A9
   tiler.
6. **Measured vertex forms only.** Our vertex backend refuses anything Apple's probes didn't show.
   Zink's depth-remap VS is byte-equal to Apple's `v_depth`. His vertex codegen is A10-validated only.
7. **Evidence and refusal JSON.** `g5p-shaderc`'s `path`/`evidence`/`temps` output drives the shader gates
   (`wsi.sh`, `gpu-demo.sh`). His compiler prints to stderr.
8. **Structure.** His output feeds `pvr_*` pipelines (`struct g9p_shader`, the `PVR_HAS_FEATURE(apple_g9p)`
   gates in upstream code). Ours is a separate driver (`vulkan/g5p`, `pco_g5p_program`). Replacing it means
   choosing one skeleton first (a9-reply §3.2), not a compiler swap.

**What to graft**, in order: `discards_to_select` (its zero form), the `fuse` flag (fragment only), then, each
after its own A9 check, his high-register homes and his control flow. Hyprland's shaders, which his compiler
handles, are the test corpus for those last two.

## 3. Phase 2: the port

Branch `g5p-g9p-port` from `g5p-wsi@32b83afe10c`, own worktree `build/third_party/mesa-a9-g9pport-src`, own
build directory. Each piece is its own commit with `Co-developed-by: Henrik Klee`, the
source commit hash and `Generated-by: LLM`. Copied functions keep his copyright line. Offline checks per commit:

- the round17–19 shader set through `g5p-shaderc`, old vs new, byte for byte (mrt.sh's check);
- `g5p-gfx-test`: the 32 pre-existing scenes, 34 jobs, 405 files, byte-identical (wsi.sh/mrt.sh's check),
  plus the round17 decoder;
- the wsi offline clients over the typed-V3 fake node (labwc's job shapes, `LABWC_EVIDENCE`), no refusals.

### 3.1 Commits

Mesa fork, branch **`g5p-g9p-port@2738d4c046e`** (two commits on `g5p-wsi@32b83afe10c`, worktree
`build/third_party/mesa-a9-g9pport-src`; control worktree `build/third_party/mesa-a9-g9pport-base-src`, detached at
the base):

1. **`b3eddec4eab` pco/g5p: discards in blending programs become a select (from G9P).** Henrik's
   `discards_to_select()` in its native source-over form, run after our flattening. In a program that blends
   (PREMULT or STRAIGHT_OVER) and discards, every colour store moves to the end and stores
   `bcsel(any discard condition, 0, value)`, and the hardware discard goes away. Apple's premultiplied epilogue turns
   transparent black into the tile's own colour. Unchanged: programs with a memory store, a discard or store left in
   control flow, or no discard, and programs that don't blend. An unconditional discard folds to a constant
   transparent-black colour (no select).
   - *Changes on purpose:* only blend ∧ discard programs: round 19 `discard.frag`, `rounded.frag` and round 18
     `refuse-cmp.frag` under the blend keys (404 → 548 bytes for `discard.frag` premultiplied: a select per channel
     costs more than a discard test), and the g5p-gfx-test scene `rounded-border`. The offline rule
     (`discard_select`): each lost its `discard` evidence, kept `blend`, and has a `select` or a constant colour.
2. **`2738d4c046e` pco/g5p: fuse multiply-adds in fragment programs, behind PVR_G5P_FUSE=ffma (from G9P).** NIR's
   late fusion rule, alone, as a pass on the scalar program: an fmul whose every reader is a contractable 32-bit fadd
   becomes ffmas. It is controlled by the new fragment key bit `fuse_ffma` (`PVR_G5P_FUSE=ffma`,
   `g5p-shaderc --fuse ffma`), which is off by default. It runs only with last-read groups, which is what makes a
   re-read addend safe (Henrik's `906f3eab2a6`). It never applies to vertex programs or captured shapes. Simply
   setting NIR's `fuse` option does nothing here (tried first): the rule is in `nir_opt_algebraic_late`, which this
   backend doesn't run.
   - *Changes on purpose (key on only):* round 19 `math.frag` 960 → 938, `ubo.frag` 286 → 262, round 18 `alu.frag`
     198 → 196, `rounded.frag` 804 → 802 (groups only), the port's `fma.frag` 450 → 378 and `fma-addend.frag`
     238 → 214 bytes. The offline rule (`fma_fuse`): same path, same evidence, fewer bytes. **Addend check:** in
     `fma-addend.frag`, where each addend is read again after its ffma, all 4 ffmas have C group bit 0 (selector
     byte 6, bit 7). The same detector reads 1 on all 12 ffmas of `fma.frag`, whose addends die there. With the key
     off, nothing changes.

**Not committed:** the per-render compile skip, which doesn't apply (§0); the sampler test (tex T2 has it); tile
skipping (kernel/firmware); high registers and control flow (later, §0).

## 4. Offline results (`scripts/gpu/g9p-port.sh all`)

- **build/control:** both trees go through the wsi lane's own compile lines (mrt.sh's recipe on
  `mesa-a9-wsi-v1/out`, with `-fmacro-prefix-map` back to `mesa-a9-wsi-src`). The old tree gives
  **`ac0460f9…`, the desktop ICD, byte for byte**, and its trampoline **`3387d083…`, the desktop trampoline**. So
  every comparison below is against what the desktop runs. A meson build of the same commit in another directory
  differs from `ac0460f9` only in embedded source paths (`__FILE__`, `G5P_BACKEND`), which is why the lane doesn't
  use one.
- **test:** the round17–19 regression set plus the port's shaders (`tools/mesa-a9-g9p-port/shaders`), under seven
  fragment keys: default; last-read groups; groups + premultiplied; groups + straight-over; premultiplied without
  groups (g5p-gfx-test's); and groups ± premultiplied with `--fuse ffma`.
  - **179 compiles: 144 byte-identical, 35 changed, every one explained by its commit's rule, 0 failures, no new
    refusal.** Every change is under a blend key (discard_select) or a fuse key (fma_fuse). Under the default and
    last-read keys without fusion, nothing changed.
  - **g5p-gfx-test:** 39 PASS lines both sides; 405 files / 32 job directories compared. **31 scenes
    byte-identical**, and `rounded-border` changed (discard_select, listed). The logs are identical. Round-17
    decoder: 11 scenes, 0 failed.
  - Each rule is checked both ways: without `G9P_RULES` the same trees fail (the `rounded-border` files).
- **tramp:** new trampoline `31dab5a5…`, pinned to the new ICD (VM-edit guards moved by 17888 bytes, as for the old
  ICD).
- **offline:** **PASS** (`build/gpu/g9p-port-offline.log`, 32 fake-node runs, `refused=0`):
  - **the new pair alone**, directly and through the trampoline: vulkaninfo, wsi-vk (headless and on the stub
    compositor), egl-wl (clear and triangle), vkcube-wsi, the `MESA_VK_WSI_DEBUG=sw` fallbacks, eglinfo (Zink) and
    weston-simple-egl. Every run passed, with `G5P117_INIT … result=ok … icd_sha256=344ca534…`. The only `G5P refuse`
    lines are Zink's format queries (D32_SFLOAT, 1D RGBA8), the same count on the old pair. Every job is inside
    labwc's hardware evidence set, except the clear-only and two-binding forms that the wsi lane's clients always
    had (`CLEAR_ONLY`, `BINDINGS`).
  - **labwc's job shapes, old pair vs new pair** (trampoline, as the desktop runs): wsi-vk 30 jobs, egl-wl 30,
    egl-wl --triangle 30 (+2 shader dumps), vkcube-wsi 20 (+2): **all byte-identical** (`check-port.py traces`).
  - **fusion on these clients:** the raw ICD with `PVR_G5P_FUSE=ffma` against the raw ICD without it. egl-wl
    --triangle and vkcube-wsi jobs are **identical**: neither client has a separable fmul→fadd, so **no W-client or
    labwc run can exercise fusion**. It needs its own fixture (§6).

## 5. Package

`build/gpu/g9p-port/package/g9p-port-a9.tar.gz`, sha256
**`8192a6d3b6ff127bea350d9bed54fcddc7d3eda7ce6a44bf5597e9fa1241ffa2`**:

```
344ca534ea56d83fae99bd1d8341d26d69c326d784262e0466848495a76f670c  lib64/libvulkan_g5p.so
31dab5a5f743f3fdebceee4d2e297a039968b95822f3bb39bd512832406d56e2  lib64/libvulkan_g5p117.so
20afccb5a3982eb963cd6759860d4c436a583b4d702fcaf97d91f325fff16112  COMMIT   (2738d4c046e…, the branch head)
```

The package has the ICD + trampoline pair only. EGL/GLES/gallium/GBM and the clients are the wsi package's (labwc's
tree in `/tmp/mesa102-desk/a9-mesa`, clients in `/tmp/wsi-a9/a9-mesa`), which this lane doesn't change. The base is
`g5p-wsi`, so like the desktop ICD `ac0460f9` it does **not** have `g5p-wsi-fix`'s refusal of vertex shaders
above 12 temps (`4585835ac8c`). Cherry-pick that commit when this branch is combined with wsi-fix.

## 6. Device plan (orchestrator): **warm-safe**

`scripts/gpu/g9p-port-desk.sh` (run from the workspace, `G9P_RUN=<stamp>` keeps one output directory under
`build/gpu/g9p-port/device-<stamp>`).

**Base and safety:** the running desktop boot (deskfold or later, labwc on the WSI pair `ac0460f9`/`3387d083`,
linux-dmabuf on). There is no kernel change, no reboot, no kexec and no firmware word. Phases 2–4 restart labwc, as
`build/gpu/wsi/damage-only.sh` does. **What changes on the GPU:** by default, nothing in labwc's or the W1–W3
clients' jobs. Offline, their traced jobs are byte-identical to the desktop ICD's (§4). The only programs that
change are blend ∧ discard programs (none in labwc's set) and, with `PVR_G5P_FUSE=ffma` (which the trampoline
nulls), fused fragment programs. So this run checks for regressions, not new forms. **Stop rule:** any
`RENDER-V3 terminal kind=timeout|fault` or `complete=-` stops the run. Then `restore`, keep the session (a warm
kexec is fine on deskfold+ even after a failed context).

```sh
export G9P_RUN=$(date -u +%Y%m%dT%H%M%SZ)
scripts/gpu/g9p-port-desk.sh stage     # package checks, /tmp/g9p-a9 + client tree; prints the desktop's pair (expect ac0460f9… 3387d083…)
scripts/gpu/g9p-port-desk.sh clients   # W1 wsi-vk, W2 egl-wl, W3 vkcube-wsi on the new pair beside labwc (stop after a bad rung)
scripts/gpu/g9p-port-desk.sh soak-a    # control: labwc damage-only on its current pair, 120 s soak (owner: drag, type, keyboard)
scripts/gpu/g9p-port-desk.sh soak-b    # the new pair (old one kept as *.pre-g9p), the same 120 s soak
scripts/gpu/g9p-port-desk.sh restore   # back to the pre-g9p pair, damage-only (the rollback; run it if soak-b fails)
```

**PASS:**

- **stage:** `contents-ok`. The desktop pair is `ac0460f9…`/`3387d083…`. If it is `a2cb9215…`/`4a895f6d…`
  (wsi-fix), note it: soak-a is then the wsi-fix control, and the comparison still holds except for the
  >12-temp refusal.
- **clients (W1–W3), as wsi.md's rungs:** `G5P117_INIT … result=ok … icd_sha256=344ca534…` in every client log.
  No `G5P refuse` other than Zink's D32 format query. One `RENDER-V3 bind` on a pair other than labwc's, `bad=0`,
  `fbdiff … result=PASS`, W1–W3 `frames=600 bad=0 … result=PASS`, W3 `check frame=30 … result=PASS` (the
  textured cube in `vkcube-wsi-30.ppm`), and labwc's FRAME advancing.
- **soak-b vs soak-a** (`soak-*/stats.txt`, `marks.txt`):
  - `g5p_refuse_nonformat=0 arena_refusals=0 v3_slow_hold_terminal=0` in both;
  - soak-b's `icd=` shows `344ca534…`;
  - **frame-gap median and per-frame GPU time (fin) median in soak-b ≤ soak-a × 1.05.** Reference:
    damage-only on `ac0460f9`, 683 frames, frame gap median 0.203 s, fin median 41 ms
    (`build/gpu/wsi/damage-only-20261101T211343Z`). The jobs are the same bytes, so a real difference would point
    at something outside the ICD (thermal, input load). Repeat soak-a after soak-b before calling it a regression;
  - the owner sees no stale rectangles and no missing pixels.
- **restore:** the `.pre-g9p` pair is back (`restore.txt` sha256s), and labwc comes up damage-only.

**Not in this run** (each needs its own fixture before labwc gets it):

- **Fusion on the desktop:** the trampoline's `policy()` nulls every unknown `PVR_G5P_*`
  (`tools/mesa-a9-production117/g5p117-icd.c:129-160`). Enabling `PVR_G5P_FUSE=ffma` needs a `P_PASS` line there,
  which is a trampoline-source change (new `TRAMP_SRC` control), the owner's call. Alternatively, fly a raw-ICD
  fixture first (fma.frag through `g5p-shaderc --fuse ffma` against a CPU reference).
- **Blend ∧ discard:** a fixture drawing `blend-discard*.frag` premultiplied over a known background, overlapping
  another draw in the same 32×32 tiles (Henrik's failure shape). PASS: the overlapped object keeps its colour in
  every tile, and the discarded pixels keep the background.
