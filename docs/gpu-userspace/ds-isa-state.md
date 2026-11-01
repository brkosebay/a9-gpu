# G5P fragment state — sampler, image, blend, end-of-tile (cross-check lane)

2026-10-23. Offline only: **no device access, no GPU execution, no commits, no
edits** to `isa-g5p.md`, `g5p_usc.py` or `g5p_usc_resources.py` (the ISA lane
owns those). This document is the *state* half of the ISA round-2 pair: the ISA
lane works instruction encodings starting with vertex-stage uniform reads; this
lane covers what the driver writes into descriptor memory and what that implies
for the fragment program.

Sources are labelled, because they differ in strength:

| Tag | Source | What it can prove |
|---|---|---|
| **cap** | iOS 16.7.10 render captures under `build/gpu-userspace/device/out5-*` (round-6 `tex`/`pass` scenes), read with `tools/gpu-userspace/analyze-round6-captures.py` | that a field exists and its value for the captured state |
| **drv** | offline `AGXMetalA9` (shared-cache dylib, `build/gpu-userspace/dylibs/`), Ghidra project `build/gpu-userspace/ghidra/agxmetala9`, decompiles in `build/gpu-ds/isa-state/decomp/` | the encode function and every bit position it writes |
| **cmp** | differential probes compiled by Apple's own `metal-tt` (`applegpu_g5p`, `air64-apple-ios16.0`, metalfe-32023.921.6), 83 fragment programs | which state reaches the compiler and how it changes code |
| **mesa** | stock Mesa `src/imagination` (Rogue/PowerVR), `third_party/mesa-g5p-gfx` | independent model of the same hardware family; never an encoder substitute for G5P |

A claim is only made when the tags it cites agree, or when a single source is
unambiguous and named. Anything not covered says so.

```sh
bash scripts/gpu-userspace/check-isa-state.sh            # compile + analyse + tests
bash scripts/gpu-userspace/check-isa-state.sh --unit-only # portable, no Xcode
```

Artifacts: `build/gpu-ds/isa-state/corpus/` (source, metallib, split objects,
`evidence.json`, `validation.json`), committed reference vectors in
`tools/gpu-ds/vectors/isa_state_vectors.json`.

## 0. Summary

1. **The sampler word is solved.** The 8-byte sampler state is a complete,
   documented 64-bit field set: LOD clamps, anisotropy, min/mag/mip filters,
   three wrap modes, normalized/pixel coordinates and compare state. Two
   independent encodes (nearest/clamp capture and the driver's constructor)
   agree bit for bit, and 32 compiler probes confirm every enum value the
   compiler emits. **The A9 has no border-colour and no mirror-clamp address
   mode**: the driver rejects both and its three capability queries all
   return 0.
2. **The image descriptor is solved for 2-D BGRA8/RGBA8 and structurally for
   the rest.** The block is reproduced byte for byte for the captured 4×4 BGRA8
   texture, and the format qword for both 64×64 PBE targets. The format key's
   internal structure is proven by the BGRA8↔RGBA8 pair (they differ only in the
   four channel selectors). R8 and sRGB keys are *predicted* from the driver's
   per-format table and need one capture each.
3. **Blending is not a state word.** AGXMetalA9 has exactly three state
   encoders — sampler, image, PBE. Blend factors and operations are packed
   into `AGX::FragmentProgramKey` and therefore compile into the fragment
   program, which reads the tile (`1c … 28`), blends in f16 and writes it back
   (`1c … 08`). 17 blend states, 2 explicit-framebuffer-fetch probes and the
   Mesa `nir_lower_blend` path agree. The ISA lane's own premultiplied
   assembler reproduces Apple's 124-byte program **byte for byte**.
4. **fragcoord, point coord, discard, depth.** `[[position]]` uses the raster
   unit (`1c … 68/69`, `08 08/08 09`); `[[point_coord]]` arrives through two
   ordinary iterators rather than the raster unit (inferred: a varying);
   `discard_fragment()` is the `04 68` token with two conditional forms
   (`04 60`, `04 61 SS 00`) and no state word; `[[depth(any)]]` compiles to a
   fourth token, `04 21 04 00`, measured here for the first time.

## 1. Sampler state: the 8-byte word is complete

### 1.1 Where it lives

The capture puts the sampler at GPU `0x1001d0020`, 8 bytes, inside the same
descriptor page as the 32-byte image block at `0x1001d0000` (**cap**; the
fragment loader DMAs 8 image dwords into common0…7 and 2 sampler dwords into
common8…9, controls `10000008`/`90010002`). The USC sample instruction names
them as qword selectors: `1a f5 00 01 08 80` = 2-D sample, image qword 0,
sampler qword 4 (`b3>>1`, `b4>>1`). The whole program is 20 bytes and is
*identical for all 24 non-compare sampler probes*:

```
08 13 00 03 00 80    iterator (4-byte form + extension): varying -> halves 0..3
1a f5 00 01 08 80    sample 2-D, image qword 0, sampler qword 4
4d 00                wait
1c 41 00 08          emit 4 dwords (an RGBA32Float target) from half 0
00 80                end
```

A compare sampler is a different instruction and a 60-byte program, but the
eight compare *functions* are byte-identical to each other: only "is a compare
sampler" is compiled, the function itself is runtime state.

### 1.2 Field table

All positions below are from
`AGX::SamplerStateEncoderGen2<kAGXRevG5>::SamplerStateFields::SamplerStateFields(AGX::SamplerDescriptor const&)`
at **0x207d80720** (**drv**), with the post-processing done by the
`AGXA9FamilySampler` initialiser at **0x207d80588** (**drv**).

| Bits | Field | Encoding | Evidence |
|---|---|---|---|
| 0–9 | `lodMinClamp` | `floor(clamp(x,0,14)·64)` | **drv**; **cap** = 0 |
| 10–19 | `lodMaxClamp` | same; `FLT_MAX` saturates to 896 | **drv**; **cap** = 896 = 14.0 |
| 20–22 | max anisotropy | `ceil(log2(n))`, `n` = 1,2,4,8,16 | **drv**; **cmp** 5 probes (1:1 … 16:16) |
| 23–24 | mag filter | 0 nearest, 1 linear, 2 unspecified | **drv**; **cmp** 8 probes |
| 25–26 | min filter | 0 nearest, 1 linear, 2 unspecified | **drv**; **cmp** 8 probes |
| 27 | mip filter = nearest | only written when normalized and mip ≠ none | **drv**; **cmp** 3 probes |
| 28 | mip filter = linear | same | **drv**; **cmp** 3 probes |
| 29–31 | address U | 0 edge, 1 repeat, 2 mirror, 3 clamp-to-zero | **drv** address switch; **cmp** 8 probes; **cap** = 0 |
| 32–34 | address V | same | idem |
| 35–37 | address W | same | idem |
| 38 | pixel (unnormalized) coordinates | 1 when `coord::pixel` | **drv** (`^0x4000000000` after `bit14<<24`); **cmp** 1 probe |
| 39–41 | compare function code | 0 le, 1 ge, 2 lt, 3 gt, 4 eq, 5 ne, 6 always, 7 never | **drv** two tables `DAT_207ddec60`/`DAT_207ddecb0`; **cmp** 8 probes; **cap** = 7 |
| 42 | compare enabled | set for every runtime sampler (default compare is *never*) | same |
| 43–52 | zero | cleared by the sampler object | **drv** 0x207d80588 |
| 53 | caller bit (descriptor bit 15) | 0 in every capture | **drv** |
| 54–55 | sampler kind | `0b11` ordinary; the driver's other branch also sets bits 43–46 and 49–55 | **drv**; **cap** = 3 |

Decoded capture, `00 00 0e 00 80 07 c0 00` = `0x00c00780000e0000`:

```
lod 0.0 .. 14.0 (14 levels, the driver clamp), anisotropy 1, mag/min nearest,
no mip filter, clamp_to_edge on U/V/W, normalized coordinates,
compare "never" enabled, sampler kind 0b11
```

`tools/gpu-ds/isa_state.py` reproduces those eight bytes exactly
(`test_isa_state.py::capture/sampler-word-encode`) and provides
`encode_sampler_state` / `decode_sampler_state` for the full field set.

### 1.3 Border and mirror-clamp do not exist on this part

| Metal mode | Driver result | Evidence |
|---|---|---|
| `clamp_to_edge` (0) | word value 0 | **drv** switch case 0 |
| `mirror_clamp_to_edge` (1) | **rejected**, 0xffffffff | **drv** switch `default`; `-[AGXA9FamilyDevice supportsMirrorClampToEdgeSamplerMode]` returns 0 |
| `repeat` (2) | word value 1 | **drv** |
| `mirrored_repeat` (3) | word value 2 | **drv** |
| `clamp_to_zero` (4) | word value 3 | **drv** |
| `clamp_to_border_color` (5) | **rejected**, 0xffffffff | **drv** switch `default`; `…supportsBlackOrWhiteSamplerBorderColors` and `…supportsSamplerAddressModeClampToHalfBorder` both return 0 |

So the wrap field is 2 bits with exactly four meanings, and "border" is not one
of them — matching Rogue, where border colour is a separate in-memory table
indexed by tex format (`src/imagination/vulkan/pvr_arch_border.c`, **mesa**),
not a sampler mode. Emulating `CLAMP_TO_BORDER` on this GPU means either a
border texel patch or a shader-side compare, not a state bit.

### 1.4 What the compiler knows

The compiler records constexpr sampler state in `__GPU_LD_MD`; 32 probes yield
one row each (**cmp**, `evidence.json` → `sampler_schema`). The enum values the
compiler emits are the API enums, not the hardware ones:

| Schema field | Values measured | Probes |
|---|---|---|
| `address_u/v/w` | 0 edge, 2 repeat, 3 mirror, 4 clamp-to-zero (MTL enum verbatim) | 8 |
| `min_filter`, `mag_filter` | 0 nearest, 1 linear | 8 + 8 |
| `mip_filter` | 0 none, 1 nearest, 2 linear | 3 |
| `normalized` | 1 in 31 probes, 0 for `coord::pixel` | 32 |
| `comparison` | 0 none, 1 never, 2 less, 3 less_equal, 4 greater, 5 greater_equal, 6 equal, 7 not_equal, 8 always | 8 |
| `max_anisotropy` | 1,2,4,8,16 passthrough | 5 |
| `min_lod`, `max_lod` | float passthrough (0/4, 2/8, 0/14) | 3 |

Two consequences that matter for our ICD:

* The compiler's own 8-byte constant-sampler qword is a **different packing**
  from the hardware word; none of the 32 rows has `packed == hw word`
  (`test_isa_state.py::schema/compiler-qword-separate`). Copying it into the
  descriptor heap would be wrong — `g5p_usc_resources.encode_compiler_sampler`
  already says so.
* Every non-compare sampler state produces the **same 20-byte program**, so
  linear filtering, wrap modes, mips and anisotropy cost no shader change at
  all: they are runtime words. That is exactly the gap round 18 listed for
  Hyprland ("only `000e0000 00c00780` is known"): the encoder in §1.2 closes it
  for the filter/wrap/mip/lod state, and needs no ISA work.

## 2. Image (texture read) descriptor

### 2.1 Block layout

`AGX::TextureGen2<>::encodeImageReadState` (**drv**, 0x207d80bdc) calls
`AGX::ImageStateEncoderGen2<kAGXRevG5>::ImageStateFields::ImageStateFields`
(**drv**, 0x207d17ac4), which writes three qwords. The captured block is those
three qwords plus 8 zero bytes (the loader still DMAs 8 dwords).

| Bits | Field | Capture (`4×4` BGRA8) |
|---|---|---|
| 0–2 | layout enum (2 = 2-D twiddled) | 2 |
| 3 | layout flag (twiddled; linear clears it in the labelled `g5p_surface.py` experiment) | 1 |
| 5–14 | pixel-format code (driver per-format table, §2.3) | 40 |
| 15–17 | channel selector for R | 2 |
| 18–20 | channel selector for G | 1 |
| 21–23 | channel selector for B | 0 |
| 24–26 | channel selector for A | 3 |
| 27–40 | width − 1 | 3 |
| 41–54 | height − 1 | 3 |
| q1 0–38 | GPU VA ≫ 1 | `0x100000400 ≫ 1` |
| q1 63 | non-2-D / PBE target flag | 0 |
| q2 | stride / array / 3-D fields, only written for non-2-D views | 0 |

The channel selectors are the one field whose *meaning* is proven rather than
assumed: RGBA8 and BGRA8 differ in exactly those 18 bits.

```
RGBA8  key 0x0344050a  selectors [0,1,2,3]   identity
BGRA8  key 0x0305050a  selectors [2,1,0,3]   R comes from component 2
XOR of the two qwords: 0x00410000 (nothing else differs)
```

`tools/gpu-ds/isa_state.py::encode_image_state` reproduces:

* the 4×4 BGRA8 texture block (**cap**, 24/24 bytes),
* the 64×64 BGRA8 PBE qword (`00007e01fb05050a`, **cap**),
* the 64×64 RGBA8 PBE qword (`00007e01fb44050a`, **cap**),

and `decode_image_state` inverts it. Note the round-6 report prints PBE qwords
as numeric values, not byte order; the test vectors record that convention.

### 2.2 sRGB is *not* in the shader

Twelve probes hold the fragment body fixed and change only the attachment
format (**cmp**): BGRA8, RGBA8, and both `_sRGB` variants compile to the **same
12-byte program** with and without blending. Only two formats change the code:
`R8Unorm` (one-channel target → one output word instead of two) and
`RGBA16Float` (shorter blend epilogue, 70 vs 110 bytes, because no f16
round-trip is needed).

Therefore the sRGB encode on write and decode on read must live in the
image/PBE state, not in the program — and the driver's own key struct says
exactly where for the write side: `_AGCDrawBuffer.gammaCorrection:1` plus
`pbeSourceFormat:5` (the PBE's source format). The same struct also explains
the two formats that *do* change the shader: `numWrittenComponents:3` is why an
`R8Unorm` target emits one word instead of two, and `preEmitSwizzle:3` is the
channel-order selection the PBE applies after the program (which is why BGRA8
and RGBA8 share a program). The driver's per-format table has an
explicit gamma field for exactly this: linear formats store 0, both `_sRGB`
rows store 3 (**drv**, table rows below). What is *not* yet measured is which
key bits that gamma field lands on — see §6.

### 2.3 Per-format rows the driver actually uses

`texFormat*` are real symbols in AGXMetalA9 `__TEXT`, 0x60 bytes per row
(**drv**). Only the fields the image-state encoder consumes are shown; `+0x00`
is the value shifted into bits 5–14 and `+0x58` is the gamma field.

| Format | +0x00 code | channels | +0x2c row | gamma (+0x58) | bytes/texel (+0x5c) | Key |
|---|---|---:|---:|---:|---:|---|
| `BGRA8Unorm` | 0x28 | 4 | 0x34 | 0 | 4 | **0x0305050a** (cap) |
| `RGBA8Unorm` | 0x28 | 4 | 0x31 | 0 | 4 | **0x0344050a** (cap) |
| `ARGB8Unorm` | 0x28 | 4 | 0x35 | 0 | 4 | 0x0221850a (predicted) |
| `BGRA8Unorm_sRGB` | 0x28 | 4 | 0x34 | **3** | 4 | see below |
| `RGBA8Unorm_sRGB` | 0x28 | 4 | 0x31 | **3** | 4 | see below |
| `R8Unorm` | 0x00 | 1 | 0xb5 | 0 | 1 | 0x04b4000a or 0x0590000a (both predicted) |
| `A8Unorm` | 0x00 | 1 | — | 0 | 1 | not modelled |
| `RG8Unorm` | 0x0a | 2 | 0xb6 | 0 | 2 | 0x0584014a (predicted) |
| `RGBA16Float` | 0x32 | 4 | — | 0 | 8 | 0x0344064a (predicted) |
| `R32Float` | 0x21 | 1 | — | 0 | 4 | 0x0590042a (predicted) |
| `RGBA32Float` | 0x38 | 4 | — | 0 | 16 | 0x0344070a (predicted) |

Predicted keys come from the measured structure (`layout | code≪5 | selectors`)
with the driver's format code; they are **not** measured.
`test_isa_state.py` keeps every predicted format out of
`IMAGE_FORMAT_KEY_CAPTURED`, so no test can silently promote one.

The sRGB rows need a sentence of their own: the encoder has **no gamma
parameter** — only two integers that reach bits 5–14 (`param_3≪5`,
`param_4≪12`, and `param_4` is 0 in every captured case). Since sRGB differs
from linear only in the driver's `+0x58` gamma field (0 vs 3), the gamma value
must be folded into those integers by the caller. Our model therefore produces
*the linear key* for an sRGB format today; the next capture (§6) tells us which
bits carry it. Writing `BGRA8Unorm_sRGB` as `0x0305050a` would be a guess, so
the encoder refuses to claim otherwise.

### 2.4 Cross-check against Mesa's Rogue image state

Mesa's `TEXSTATE_IMAGE_WORD0` (`src/imagination/csbgen/rogue/texstate.xml`,
**mesa**) describes the same *concepts* with a different layout:

| Concept | Mesa Rogue | G5P (measured) |
|---|---|---|
| texture type | bits 0–2 | bits 0–2 (**same**) |
| gamma | bit 3 (+ bit 4 for 2-component formats) | not located (bit 3 = a layout flag in both captures) |
| channel swizzles | four 3-bit fields at bits 5–7 (A), 8–10 (R), 11–13 (G), 14–16 (B) | four 3-bit fields at bits 15–26, ascending R, G, B, A |
| format | 7-bit enum at bits 27–33 | 10-bit code at bits 5–14 |
| width − 1 / height − 1 | bits 34–47 / 48–61 | bits 27–40 / 41–54 |
| sample count | bits 62–63 | not in the captured 24 bytes |

The channel selector *values* do agree: Mesa's `SWIZ` enum is 0–3 = source
components 0–3, and the two G5P captures use exactly 0–3 with the expected
BGRA/RGBA permutation. What does not transfer is every bit position after bit 3
— the same warning `isa-g5p.md` gives for the USC encoding. Only 4 and 5
(each a constant) remain ambiguous, and Mesa's enum has 4 = ONE, 5 = ZERO
while the G5P driver table could not be read that far; both candidates are
kept explicit in `isa_state.py` (`MISSING_CHANNELS`).

## 3. Colour blending is compiled into the fragment program

### 3.1 There is no blend state word

The dylib's 5,676-symbol table contains exactly three `*StateEncoder*` classes:

* `SamplerStateEncoderGen2<kAGXRevG5>` (§1),
* `ImageStateEncoderGen2<kAGXRevG5>` (§2),
* `PBEStateEncoderGen3<kAGXRevG5>` (render-target state; format/dims/address/
  stride, no blend fields).

There is no ISP/blend encoder and no symbol containing "blend" other than the
Objective-C descriptor accessors. `AGX::FragmentProgramKey<…>::CreateFragmentProgramKey<>`
(**drv**, 0x207ca628c) reads the blend state from the API descriptor and packs
it into a per-attachment key word:

| Key bits | Field | Packing from the decompile |
|---|---|---|
| 0 | blending enabled | flag |
| 1–5 | source RGB factor | `(factor+1)·2` |
| 6–10 | destination RGB factor | `(factor+1)·64` |
| 11–15 | source alpha factor | `(factor+1)·0x800` |
| 16–20 | destination alpha factor | `(factor+1)·0x10000` |
| 21–23 | RGB operation | table `DAT_207de34b0`: Add→`0x600000`, Sub→`0x800000`, RevSub→`0xa00000`, Min→`0x200000`, Max→`0x400000` |
| 24–26 | alpha operation | table `DAT_207de34c4`: Add→`0x3000000`, Sub→`0x4000000`, RevSub→`0x5000000`, Min→`0x1000000`, Max→`0x2000000` |

Apple's own DWARF types in the dylib name the same fields and widths.
`FragmentProgramKey` embeds `_AGCFragmentContextStateAGP`, whose members are

* `fragmentState` — `alphaToCoverage`, `alphaToOne`, `sampleCoverage`,
  `sampleCoverageInvert`, `depthStencilWriteDisabled`, `logicOp` +
  `logicOpState:5`, `alphaTest` + `alphaTestFunc:4`, `smoothPoints`,
  `pointSpriteEnabled`, `outputPointSize`;
* `colorMaskState` — `red/green/blue/alphaEnableBits:8` each;
* `blendState[8]` — `_AGCBlendState = enable:1, src:5, dst:5, srcA:5, dstA:5,
  equation:3, equationA:3, unused:5`;
* `pcf` — `_AGCPCFStateAGP.depthCompareClampMask:32`;

plus `_AGCDrawBufferState` with `_AGCDrawBuffer[8] = pbeSourceFormat:5,
numWrittenComponents:3, gammaCorrection:1, preEmitSwizzle:3,
outputPartitionOffset:8, unused:12`. The decompiled packer and the DWARF widths
agree field for field, and the three structs that describe words each fill
exactly 32 bits (`test_isa_state.py::key/*`).

A state that participates in a *program key* selects a compiled program; it is
not uploaded per draw. Note what that list includes: **colour mask, alpha test,
alpha-to-coverage, logic ops and point-sprite mode are key bits too**, so none
of them is a hardware state word on this part either. The ISA lane reached the
same conclusion from the other direction ("the seven destination-dependent
cases insert `1c 21 04 28` before ALU"), and Mesa does the same thing for this
hardware family: `pco_nir.c` runs `nir_lower_blend` on fragment shaders, and the
lowering emits tile loads/stores through the Rogue special registers
(`PCO_SR_TILED_LD/ST_COMP0..7`).

### 3.2 The compiled epilogue

Same fragment body (`return in.a;`), 17 attachment states (**cmp**). Every
destination-dependent case starts with the *tile read* and ends with the
*emit*; the register convention is stable across all of them:

```
halves 0..3   source colour  (the varying iterator writes here)
halves 4..7   destination    (tile read writes here, always dst_half = 4)
halves 0..3   result         (emit always reads from half 0)
```

| Blend state (src / dst / op) | Bytes | Tile read | Notes |
|---|---:|---|---|
| off (blending disabled) | 12 | – | `0a 03 00 02 · 4d 00 · 1c 21 00 08 · 00 80` |
| One / Zero / Add ("replace") | 12 | – | only the iterator's saturate bit differs from *off* |
| SourceColor / Zero / Add | 52 | – | source-dependent only |
| OneMinusSourceAlpha / Zero / Add | 56 | – | source-dependent only |
| One / One / Add | 104 | 2 words | needs the destination even with factor One |
| DestinationColor / Zero / Add | 104 | 2 words | source factor references the destination |
| One / One / RevSub | 104 | 2 words | |
| SourceAlpha / OneMinusSourceAlpha / Add ("straight") | 110 | 2 words | source alpha used straight from half 3 |
| SourceAlpha / One / Add | 110 | 2 words | |
| One / OneMinusSourceAlpha / Add ("premultiplied") | 124 | 2 words | computes `1−srcA` once into half 12, reuses it |
| DestinationAlpha / OneMinusDestinationAlpha / Add | 130 | 2 words | |
| One / One / Subtract | 144 | 2 words | |
| One / OneMinusSourceColor / Add ("screen") | 160 | 2 words | |
| One / One / Min | 162 | 2 words | |
| One / One / Max | 162 | 2 words | |
| SourceAlphaSaturated / OneMinusSourceAlpha / Add | 166 | 2 words | |
| BlendColor / OneMinusBlendColor / Add | 200 | 2 words | reads blend constants from the common store |

Measured rule (`blend_needs_destination`): a tile read appears exactly when a
factor references the destination (`Destination*`, `SourceAlphaSaturated`), when
the destination factor is not `Zero`, or when the operation is Min/Max/Subtract.
13 of the 17 states read the tile and 4 do not (off, replace, source-colour-only,
one-minus-source-only); every one of the 17 matches the rule.

Two more measured details:

* **Blending enabled sets the saturate bit on the varying iterator** (`0a 03 00
  02` → `0a 03 01 02`, bit 0 of the iterator's byte 2). Source colour is
  clamped before the blend math, as D3D/Vulkan require.
* **A partial write mask is a read-modify-write too.** Mask `rg`/`rgb`/`r`
  compiles to 16/46/26 bytes with a 1- or 2-word tile read; mask 0 compiles to
  the two-byte terminator and nothing else. So the write mask is not a pure
  store-enable on this GPU either.

### 3.3 The end-of-tile tokens

| Token | Meaning | Where measured |
|---|---|---|
| `1c ((words<<4)|1) dd 08` | write `words` dwords from half `dd` to the tile | every program's last instruction |
| `1c ((words<<4)|1) dd 28` | read `words` dwords of tile colour into half `dd` | 27 programs: 13 blends, 6 blended format variants, 3 partial masks, 3 MRT, 2 fetches |
| `1c ((words<<4)|1) dd 29 04 00` | long form, used when the destination half ≥ 16 (RT1-only blend) | `f_mrt_01` |
| `1c ((words<<4)|1) dd 68` | raster X/Y | `f_raster_pos_xy`, `f_raster_pos_all` |

`words` is 1, 2 or 4. For an 8-bit RGBA target the emit is 2 words (four f16
halves); for `R32Float`/`RGBA32Float` targets it is the number of 32-bit words
(4); for `R8Unorm` it is 1. Two render targets with blending enabled on both
produce one **4-word** tile read (`f_mrt_11`), i.e. the two destination colours
come back in a single read.

The ISA lane already exposes `encode_tile_read`/`encode_emit` for these; its
`assemble_premult_blend_fs('BGRA8Unorm')` output equals Apple's 124-byte
`f_blend_premult` **byte for byte** (`test_isa_state.py::cross-lane/*`), which
is independent confirmation of both lanes' reading.

### 3.4 What this means for the ICD

* Alpha-blended Hyprland surfaces cannot be enabled by setting a state word.
  Either the shader compiler emits the blend epilogue (as Apple's does, keyed
  by blend state), or blending is done in a second pass. The first option is
  what this GPU expects; the round-18 backend already has the pieces
  (`tile_read`, `half_binary`/`half_fma`, `tile_component`, `emit`).
* The program cache key must include the blend state, or pipelines will share
  the wrong program. The key fields are in §3.1.
* The destination comes back as four f16 halves (which represent every 8-bit
  unorm value exactly), so no precision is lost reading an 8-bit target. A 16F
  target uses a shorter epilogue (70 vs 110 bytes) because the f16 round-trip
  disappears, which is why the attachment format must be part of the key too.

## 4. gl_FragCoord, point coord, discard, depth

| Feature | Encoding | Probes | Bytes |
|---|---|---|---|
| `[[position]].xy` (fragcoord) | `1c 21 dd 68` raster XY (unsigned fixed point /16), then ALU | 2 | 62, 70 |
| `[[position]].z` | `08 09 00 02` iterator | 1 | 38 |
| `[[position]].w` | `08 08` iterator | 1 | 36 |
| `[[position]].xy` scaled | same as xy, one extra multiply | 1 | 62 |
| `1/position.w` | xy read + reciprocal refinement | 1 | 106 |
| `[[point_coord]]` | **two ordinary iterators** (`08 11 00 02`, `08 13 04 0a`), no raster-unit token | 1 | 16 |
| `[[front_facing]]` | move-immediate + class-0x9a literal op (`b9 61 58 00`); the facing bit's source is not decoded | 1 | 42 |
| `[[sample_id]]` | `08 1b 00 04` + class `40` + four move-immediates | 1 | 68 |
| `discard_fragment()` | `04 68` | 1 | 8 |
| conditional discard | `04 60` (predicate already set) or `04 61 SS 00` | 2 | 74, 46 |
| `[[depth(any)]]` | `04 21 04 00` | 1 | 32 |

State-side conclusions:

* **fragcoord needs no fragment-state word.** The raster unit supplies X/Y in
  fixed point and the shader divides by 16; Z and W arrive through the ordinary
  iterator path. The per-target mapping is therefore in the PPP viewport /
  region-clip block the recorder already emits (`round6-capture-report.md`), not
  in the descriptor heap. The *scaled* probe compiles to the same size as the
  plain one, so no origin/scale convention is baked into the program.
* **point_coord is (inferred) a varying.** It arrives as two ordinary class-0x08
  iterators, never through `1c … 68`, and no point-size/point-coord state word
  exists in the three encoders; `pointSpriteEnabled`, `smoothPoints` and
  `outputPointSize` are, again, `_AGCFragmentState` key bits. If GL point
  sprites are ever exposed the driver will have to supply the coordinate through
  the varying stream.
* **discard has no state word here.** No alpha-test or coverage state appears in
  the driver's encoders; `_AGCFragmentState.alphaTest`/`alphaTestFunc`/
  `alphaToCoverage`/`sampleCoverage` are **fragment program key** bits, so they
  select a compiled program exactly like blending does. The discard token alone
  kills the fragment, and the write mask (§3.2) controls what the store keeps.
  Mesa reaches the same shape (`nir_lower_alpha_to_coverage` +
  `nir_opt_peephole_select` in `pco_nir.c`).
* `[[depth(any)]]` is a *fragment-side* store token, not a PBE field — relevant
  because round 18 refused depth feedback and no depth store had been measured.

## 5. Evidence ledger

| Item | Count | Where |
|---|---:|---|
| fragment probes compiled (this lane) | 83 | `build/gpu-ds/isa-state/corpus/isa_state.metal` |
| split objects (probes + shared vertex) | 166 | same, `split_g5p/` |
| sampler probes (runtime state, metadata rows) | 32 | §1.4 |
| blend-state probes | 17 | §3.2 |
| attachment-format probes (blend / no blend) | 6 + 6 | §2.2 |
| write-mask probes | 4 | §3.2 |
| MRT probes | 3 | §3.2 |
| explicit framebuffer-fetch probes | 2 | §3.2 |
| raster probes (fragcoord/point/facing/sample id) | 9 | §4 |
| discard probes / depth probe | 3 / 1 | §4 |
| analyzer assertions | 92, 0 failures | `evidence.json`, `validation.json` |
| portable unit checks | 372, 0 failures | `test_isa_state.py` |
| driver functions decompiled | 14 files | `build/gpu-ds/isa-state/decomp/` |
| driver debug types read (field names/widths) | 5 | `strings AGXMetalA9` → `_AGC*` DWARF |
| captured words reproduced byte-exactly | 4 (sampler, image block, 2 PBE qwords) | §1.2, §2.1 |
| bytes of Apple code attributed to blend state | 12 … 200 per state | §3.2 |

Files added by this lane:

```
tools/gpu-ds/isa_state.py             field tables, encoder/decoder, classifiers
tools/gpu-ds/gen_isa_state.py         probe generator (Metal + pipeline JSON)
tools/gpu-ds/analyze_isa_state.py     evidence + validation + reference vectors
tools/gpu-ds/test_isa_state.py        portable tests (372 checks)
tools/gpu-ds/vectors/isa_state_vectors.json  committed reference vectors
scripts/gpu-userspace/check-isa-state.sh     reproduction entry point
```

`g5p_usc.py`/`g5p_usc_resources.py` are imported read-only; nothing in this lane
edits them, and `isa-g5p.md` is untouched.

The kernelcache was checked rather than used: `build/kc/kc.strings` (iOS
16.7.10, iPad6,11) contains no `SamplerStateEncoder`, `ImageStateEncoder`,
`PBEStateEncoder` or `BlendState` string, so all three state encoders and the
fragment program key live in userspace and the kernel only carries the
resulting buffers. `scripts/ghidra.py` against the shared kernelcache would not
add anything to §1–§4.

## 6. Open items and the cheapest captures that close them

| Gap | Why it matters | One capture closes it |
|---|---|---|
| sRGB texture key | the only unknown left for sRGB sampling; the shader is proven identical | a 4×4 `BGRA8Unorm_sRGB` texture in the same scene as the existing linear one: diff the 32-byte descriptor |
| R8/RG8 texture key **and** the 4/5 constant polarity | one-channel sampling is used by masks/coverage | a 4×4 `R8Unorm` (and `RG8Unorm`) texture descriptor, 32 bytes each |
| mip/aniso runtime words | the encoder is complete but only nearest/no-mip has been captured | one sampler pair: `linear`+`repeat`+`mip_filter::linear`, `max_anisotropy(4)` |
| blend on hardware | the tile read is compiled, never observed executing | one draw with `src_alpha/1−src_alpha` on BGRA8, then read back the tile/pixels |
| MRT half allocation | RT1's colour lives at half 16 in the long-form read, but the emit's layout is not decoded | one 2-RT blend capture |
| `1c … 29` long form | only seen for RT1 | same capture |
| point-coord slot | allocator-assigned varying; no fixed slot | a point primitive with `[[point_coord]]` |
| `04 21 04 00` | depth store token, association only | a depth-writing fragment shader capture |

Nothing in §3 changes round 18's "still refused" list by itself: the blend
epilogue is a *compiler* feature, so the next step is the ISA/backend lane
wiring `tile_read` + half ALU + `emit` into `pco_g5p_general.c` behind a blend
key, with `assemble_premult_blend_fs` as the first regression. What this lane
adds is that the *state* side of the question is now closed: there is no blend
word to upload, and the sampler word that Hyprland needs is fully specified.
