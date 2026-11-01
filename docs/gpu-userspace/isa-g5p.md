# G5P USC ISA — ISA round-2 follow-up to round 18, 2026-10-23

**Verified** below means a rule checked against compiler-produced bytes, with
manifest/source assertions in `g5p_isa.py verify` or `check_isa_round2.py`. It does not
mean the instruction has executed under Linux or every bit is understood.
**Inferred** names describe a plausible operation whose full semantics remain
unmeasured. Unknown fields are preserved as raw bytes, never synthesized.
This is the complete specification of the currently characterized subset,
not a claim of a complete hardware ISA.

**Latest:** [ISA round 2](#isa-round-2-follow-up-to-mesa-round-18) adds
509 named source probes, compiled in **both fast and strict math modes**,
VS constant-buffer/mat3 dataflow, compare/select recipes, math-lowering
fields, fragment coordinates, computed texture coordinates, driver-derived
samplers and independently authored premultiplied blend reference programs.
Its scope and remaining unknowns supersede the historical round-18 gap list.
All new evidence is offline; there was no device access or shader execution.

ISA round 18 adds field encoders in `tools/gpu-userspace/g5p_usc.py` and
`g5p_usc_resources.py`, with **383 ALU/control + 204 resource/state probes**
compiled locally through the existing `metal-tt` path. No device access,
GPU execution, workspace commits or Mesa edits were made. The tables under
[round 18](#isa-round-18-field-encoders-and-evidence) supersede earlier
source-bank, read-LOD and backward-branch interpretations. The older
`g5p_isa.py` remains the historical decoder/regression oracle, with its
misnamed LOD flag corrected to a source selector; its coarse
confidence totals below are unchanged and do not describe the new modules.

## Identity, provenance and scope

**Verified:** the device's legacy BVNC is **9.15.6.2**, measured by the
orchestrator; see [kernel identity](../gpu/core-configuration.md). Compiler
processor names `g5p-b0`/`g5p-c0` are not BVNCs. The host oracle is Xcode's
`metal-tt -arch applegpu_g5p` for AIR targeting iOS 16; the device reference
is iOS 16.7.10. The run-1 AIR archive has 31/35 byte-identical host matches;
the remaining four have code-generation differences
(device-results.md (`device-results.md`, not published)). New round-3 probes are host-only.

**Verified:** all 113 round-1 differential shaders have byte-identical G5P
and G9P/A10 counterparts by content, versus 0/113 for G10P/A11. G5P's USC
encoding differs from Mesa's grouped Rogue encoding (`pco_isa.py`,
`pco_binary.c` at Mesa `566ed9cd9dff0a0825c7ea71fc3c31a4af03d5a7`). The
split immediate map and continuation bits cannot be repaired with a byte
permutation of that encoder. **Inferred:** G5P retains the Rogue execution
and render model with a different encoding; exact IMG versus Apple authorship
of the encoding remains unknown. PDS is a different ISA and does match the
Mesa DOUT_EXT format; see [command-stream.md](command-stream.md).

**Verified container:** GPU executables and nested shader objects are Mach-O
64-bit, CPU type `0x01000013`, G5P subtype `0x41`. Code is
`__TEXT,__text`; metadata is in `__GPU_*` segments. Outer `__vertex`,
`__fragment`, `__compute` sections establish stage. Binary archives use fat
magic `0xCBFEBABE`. `g5p_macho.py` extracts code without treating metadata or
Mach-O offsets as GPU addresses. Generated driver shaders are separate objects.

## Parcel framing and task layout

**Verified:** parcels are little-endian u16. Parcel bit **8** (high-byte bit
0) means another parcel belongs to the same instruction. The first parcel
with that bit clear ends it. The decoder rejects odd byte lengths and a
final continuation parcel with no terminator. Framing is solved for the
corpus, independently of opcode interpretation.

**Verified special parcels:** `00 c0` is alignment padding; `00 80` is the
final compute/fragment terminator. Compute commonly places the terminator
at a 64-byte boundary. Fragment code is unpadded. Characterized vertex
programs branch to a final output/endtask block at a 64-byte boundary and
do not append `00 80`. These are measured compiler conventions, not proven
hardware requirements for every possible shader.

Notation in the following sections is byte-offset based: `b0` is the first
instruction byte. A “half index” counts 16-bit register halves. `rN` and
`oN` denote 32-bit temp and output-staging registers; odd half indices are
printed `.hi`. **Inferred:** the output-staging bank can also serve as
scratch storage in multi-instruction lowering; it is not always a final
color value.

## Registers and common sources

**Verified for characterized destination forms:** `b2` is the half index,
`b3 >> 1` is the bank, excluding any instruction-specific overlaid flags.
Bank 0 is `o`, bank 1 is `r`. Other bank numbers remain unnamed. Moves,
short ALU and iterators provide the cross-checks. Texture mode and
conditional move control bits must be removed first; applying `dest_of`
blindly to those forms is incorrect.

**Verified short two-source ALU selectors:** A's half index is
`(b4 >> 1) & 63`, B's is `(b5 >> 1) & 63`. **Correction:** bit 7 is a
source-group control, not a universal temp-bank marker. Round-18 fragment
dataflow and the depth-remap VS use both values. The new decoder preserves
`a_group`/`b_group`, header and class extension; it does not assign them
physical bank names. Full source-bank selection, 64-bit pairs, indexed
registers and spills remain unresolved. Shared MOVs have a separate decoded
source layout below.

## Move immediate, including exact encoder

**Verified payload:** `98 B1 D0 D1 B4 01 B6 B7 [B8 B9]`, short 8 or long
10 bytes. Payload byte 6 bit 0 selects the long form. The characterized
complete instruction appends `54 01 38 00`, for **12/14 bytes total** under
the continuation rule. The trailer's precise execution semantics remain
**inferred**; it is retained by the encoders.

**Verified fixed bits:** b0=`98`; b1=`01` plus immediate bit 9 at bit 5;
b4 bit 0=1; b5=`01`; b6 bit 5=1; b7 bit 0=1; long b9 bit 0=1.
`D0 D1` is the destination parcel. The long-form map is:

| Immediate bits | Byte bits (verified) |
|---|---|
| 0–5 | b4[1–6] |
| 6–8 | b6[1–3] |
| 9 | b1[5] |
| 10 | b6[4] |
| 11–13 | b7[4–6] |
| 14–15 | b6[6–7] |
| 16–23 | b8[0–7] |
| 24 | b7[7] |
| 25–31 | b9[1–7] |

**Verified:** short form omits bits 16–31, including b7 bit 7. `encode_movimm`
implements the map; `selftest` checks 50 bit sweeps and `verify` checks 180
structured/random immediate functions. Long encoding of a small value is
representable but is not the default encoder choice.

**Round-18 correction:** the twelve-byte form with b6:b7=`00 41` is a
shared/common-store read; its source map and high index bit are below.
Another register move is 10 bytes,
`98 01 D0 D1 S0 S1 54 01 38 00`, with temp half index `(S0 >> 1)&63` for
the select-lowering cases. Conditional variants below share that form.

## Branches

**Verified:** six-byte form `00 C1 O0 O1 00 00`:

```
delta = (O0 & 0xfe) | ((O0 & 1) << 8) | ((O1 >> 1) << 9)
target = instruction_start + delta
```

**Verified encoding cases:** C1=`01` for ordinary forward branches and
`09` for the variant surrounding stores/conditional work. All **1,668**
round-2 branch targets checked are instruction boundaries. **Inferred:**
C1=`09` branches on an execution predicate; its source and polarity are
unresolved. The historical encoder supports even, unsigned forward deltas
below 64 KiB. Round 18 additionally verifies ten local backward branches
with sign-extended low16 deltas; see below. Structured-loop mask control
remains unresolved.

## ALU and SFU

**Verified short binary form:** class b0=`99`, length 8, destination b2:b3,
sources b4/b5 as above, operation b6. b7 bit 6 negates source B in the
characterized float add/multiply cases. Operator-only compiles isolate:

| b6 | Operation (verified within corpus) |
|---|---|
| 44 / 48 | fadd / fmul f32 |
| 04 / 08 | fadd / fmul f16 |
| 46 / 4a | fadd.sat / fmul.sat f32 |
| a1 / a2 | iadd / imul |
| c8 / ce / c6 | and / or / xor |

**Verified correction this round:** clear opcode bit 1 only for known float
saturate variants. Clearing it globally incorrectly changed `a2`, `ce`,
`c6` into unknown opcodes. Integer operator assertions now cover 80 round-2
and five independent fragment probes. Half saturate patterns `06/0a` are
recognized by symmetry but remain **inferred**, absent a dedicated sweep.

**Verified FMA subset:** class `9a`, 10 bytes, b8:b9=`4c 00`; A/B/C half
indices are `(b4/b5/b6 >> 1)&63` with temp operands in the characterized
cases. Twelve independent three-input lane combinations check
`{A,B}={x,y}, C=z`; 16 shaders check presence. Longer class-9a instructions
are not automatically FMA. IEEE corner behavior and modifier fields remain
unmeasured.

**Verified unary opcode associations:** 8-byte class `98`/`9c`, b6=`40`
floor, `80` reciprocal, `81` reciprocal square root, `84` log2, `85` exp2,
`c5` bitwise not, `e8` popcount, `e4` bit reverse. Source half selector is
`(b4>>1)&63`; b7=`80` is the measured sign-modified variant. **Inferred:**
`82`/`86` are sin/cos range-reduction core operations, not complete
standalone sin/cos; numerical precision, source-bank and exception behavior
are not specified. Generic length-mismatched `alu1/alu2` labels denote
class recognition only.

## Compute address arithmetic — new partial rules

**Verified small index add:** the 12-byte temp form is

```
99 11 DD 03 II SS 20 01 a1 09 70 00
II = (immediate << 1) | 1
SS = (source_half << 1) | 0x81
```

**Verified domain:** immediate values 1,2,3,7,16,17,63 occur in independent
`q[t+N]=p[t+N]` families. The destination is temp half `DD`; source half is
`(SS>>1)&63`. `encode_iadd_small` round-trips **231** instances in round 2
and **20** in the new probes. Its 7-bit immediate encoding is specified;
semantic tests have not swept every representable value.

**Verified scale field; inferred operation:** twelve-byte classes
`89 11`, `8d 11`, `8d 51`, with b6:b9=`00 41 a1 01`, b10=`00` and
`b11 & 31 == 6`, occur immediately before typed memory access. b11[7:5]
is log2(element stride): 0/1/2/3/4 for uchar/ushort/uint/uint2/uint4. Twenty-five
new shaders vary both size and index offset; each contains two matching
address instructions. The decoder calls this `addr.add_shift` with
**inferred instruction confidence**, even though the shift field is
verified. Source selectors b4:b7, 64-bit/carry semantics, the difference
between 89/8d, and b1=`11` versus `51` remain unknown. Do not implement a
general pointer arithmetic backend from this rule alone.

**Inferred remaining address work:** class `8a` in the common thread-index
prologue and longer `9a`/`9e` arithmetic participate in address/index
construction. No arithmetic semantics are asserted for them. Packed
instructions may encode multiple operations; splitting their bytes into
independent opcodes without the continuation rule would be incorrect.

## Iterators, memory and synchronization

**Verified iterator subset:** class `08`, length 4 or 6; `b3>>1` is varying
slot. Historical b1=`0b/13/23` associates with 1/2/4 words. New four-byte
probes also isolate `1b`=3 and `03`=4; b1 bit5's mode remains unresolved.
In the new four-byte fragment form, `b2&fe` is destination half index and
b2 bit0 tracks `saturate` (it is not simply an odd register half).
Class `0a` associates with narrowed/half values. Six-byte extensions and
interpolation/hazard modes are retained raw. These are compile/dataflow
associations, not precision or interpolation execution tests.

**Verified memory subset:** class `10` or `12`, six bytes. b1
`0b/13/23/2b/3b` selects 1/2/4/8/16 bytes. b2 bit 7 selects store versus
load; `b2&127` is data temp half index. Class `12` fixed-store examples
have an implicit argument base and b4 is the element index (verified for
32-bit stores at indices 0,1,2,4,16). General class-10 address b3:b5 remains
raw. **Inferred:** class `11` is an extended addressing variant; class `14`
loads compute arguments. Neither has complete operand semantics.

**Inferred synchronization:** class `4d` (`4d00`, `4d02`) follows loads,
iterators or textures; likely dependency waits. Class `c8` surrounds
compute stores and dispatch-mask control; precise barrier/ordering semantics
are unknown. The fixed-store assembler retains its prologue/sync templates.
A byte-exact template is not an independently authored compute prologue.

## Texture fields — round-18 selectors, unresolved runtime descriptors

**Verified implicit sample:** six-byte `1a f5 DD TT SS CC`. Explicit LOD
extends this to 8/10/14 bytes using continuation parcels. The class-`1b`
variant occurs with more live resources; its execution meaning is unknown.
All indices below are encoded fields, not measured physical GPU limits.

| Field | Encoding | New evidence (unique fragment functions) |
|---|---|---|
| Destination half | b2[5:0] | Paired texture consumers; 165 texture instructions total |
| Sample / integer-coordinate read | b2[7:6] = 0 / 1 | Read probes include explicit coordinate conversion |
| Image descriptor base | b3[7:1], **qwords** | 0,4,…,28 from 3/4/7/8-image sweeps |
| Sampler descriptor base | b4[7:1], **qwords** | 4…11,12,16,28,32 from sampler/image-count sweeps |
| Coordinate base | `(b5>>1)&63`, **dwords** | Iterator/swizzle bases 0,1,4 |
| Coordinate control | b5[7] | Both values observed; meaning unresolved |
| LOD/gradient source bank | b6[7:6]: 1 shared, 3 temp | 45 shared and 18 temp instances |
| Source low index | b6[4:0], **dwords** | 63 explicit LOD/gradient instructions |
| Gradient association | b6[5] | Four gradient probes; register layout incomplete |
| Source index[6:5] | b12[2:1] | shared34: four samples; shared66: eight samples |
| LOD control | b8[7] when present | Both values; execution meaning unresolved |

Eight-byte form ends at b7=0. Ten-byte form has b7=1, b8 control, b9=0.
Fourteen-byte form has b7=1, b9:b11=`01 00 01`, b12=`(index>>5)<<1`, b13=0.
`f5/f7/fb/fd` associates with 2D/array/3D/cube (one probe per non-2D type).
One gather probe has the eight-byte tail `00 40`; component ordering is
not decoded. Encoders refuse unisolated dimension/LOD/gradient combinations.

**Corrected LOD interpretation:** `CT=4a/4b` means **shared10/shared11**,
not a nonzero-LOD flag. Sample levels 0/1/2/4/8 have identical texture USC:
the linked literal pool supplies binary16 values to shared10. Read level0
maps a u32 LOD to shared10; nonzero reads allocate an extra kind0 common
value, moving the literal to shared11. Dynamic sample LOD selects temp2
(temp0 with a swizzle), matching a scalar `0a` iterator; dynamic read LOD
selects temp3, matching a conversion destination. Numerical precision and
exceptions remain untested.

**Verified metadata correlation:** `__GPU_METADATA` is a FlatBuffer whose
root.field0 points to a common table. common.field2 is a descriptor vector;
descriptor fields 0/2/3/4 hold kind(u8), count(u32 dwords), common destination
(u32 dwords), and source offset(u32 dwords). Kind1/2 allocate 8 image / 2
sampler dwords; kind3 associates with buffer addresses, kind5 with fixed
constant-buffer uploads, kind6 with literal-pool copies. Binding field1 is
verified for kind5 only. common.field13 contains literal bytes. Numeric
fields may be omitted/default zero. Other kinds and the complete schema
remain unknown; these paths describe this host compiler version.

Twenty-four single-binding probes form four byte-identical groups across
API bindings 0/1/2/3/7/15: API binding numbers are remapped outside USC.
Sixteen paired-binding and eight resource-count shaders isolate the two
descriptor selectors. The earlier live nearest/clamp capture independently
checks image common0…7 / sampler common8…9
(`analyze-round6-captures.py`). Runtime descriptor contents, binding remaps,
PDS upload, channels, shadow/offset/array behavior and waits remain
incomplete. This is still insufficient for general Mesa texture lowering.

## Conditional execution / predication

**Verified encoding observations:** select lowering uses class extensions
`f8/f9/fc`, which differ from `98/99/9c` by `0x60`. Normalizing that class
reveals existing move/ALU patterns. In one 10-byte register-move variant,
b3 bit 7 changes alongside the extension; it is excluded from the decoded
bank. In eight new lane probes, class-9d comparisons with b6=`58`,
b8:b9=`08 00` use b7=`41` for `<` and `81` for `>=`, with the rest of
the lane-matched shader identical. `cmp.select` exposes that relation and
keeps operand bytes raw.

**Inferred:** these are predicate-controlled writes and a relation-setting
comparison. Predicate register identity, polarity, mask stack, inactive
lanes, reconvergence and NaN behavior remain unknown. Every `.p` and
`cmp.select` instruction retains **inferred** confidence. Equality selection
of equal operands optimized away in the first probes; it is not evidence
for a decoded equality predicate. Neither `0x60` nor b3 bit 7 is a complete
predication specification.

## Fragment and vertex output, own encoders

**Verified fragment emit:** `1c N1 00 08` emits N output words, then
`00 80`. BGRA8/RGBA8 constant-color examples use two packed f16 words:
`half(r) | half(g)<<16`, `half(b) | half(a)<<16`. RGBA32Float uses four
f32 words. These are shader output words; PBE handles target packing.
`assemble_solid_fs` writes them with immediate moves into o0 onward.

**Verified vertex output:** `1e` writes an output; `5e` writes the last
output and ends the vertex task. b1 bit 2 selects temp (1) versus output
staging (0). Source is b2 in half units when present; destination slot is
b4 in six-byte forms, otherwise 0. Characterized encodings:

```
1e 14                          r0 -> slot 0
1e 15 SS 00                    temp half SS -> slot 0
1e 15 SS 01 NN 00              temp half SS -> slot NN
5e 15 SS c1 NN 00              same, last output + task end
```

**Verified:** replacing b1=`15` with `11` selects output-staging sources
for the applicable forms. Slots 0–3 are position xyzw, slots 4 onward
varyings. Nine constant-output shaders trace every written word to its
slot. `assemble_passthrough_vs` writes r0/r1/r2, branches to 64, pads with
`00 c0`, then ends with r3 -> slot3. The vertex fetch is in PDS, not this
shader's USC. Host and iPad references both match its 70 bytes exactly.
The solid red FS matches both references at 32 bytes.

**Verified prototype:** [mesa-plan.md](mesa-plan.md) describes the linked
Mesa PCO-IR backend, its independent immediate encoder, actual `pco_end`
pass, 133 checks and limitations. It does not establish stock PCO scheduling
or register allocation compatibility with G5P.

## Round 4 native conversions and fragment register model

**Verified offline differential probes:** `gen_round4.py` generates four
native 32-bit conversions at each of four input lanes. All 16 fragment
objects tile correctly, match the encoder and source-lane assertions, and
match real Mesa NIR compilation at decoded dataflow level. With the shared
vertex objects, `verify` checks 32 objects / 128 assertions, zero failures.
No exceptional-value execution or iOS compilation was performed.

```
99 01 DD DB TAG SS 20 01 OP 04
```

**Verified operation associations:**

| NIR operation | TAG | OP | Source expression in the Metal probe |
|---|---|---|---|
| `f2i32` | `13` | `60` | signed int cast of a float |
| `f2u32` | `11` | `60` | unsigned int cast of a float |
| `i2f32` | `17` | `68` | float cast of a signed integer bit pattern |
| `u2f32` | `15` | `68` | float cast of an unsigned integer bit pattern |

**Verified source field / inferred generalization:** source half-index is
encoded by `SS = 0x81 | (half_index << 1)`, tested at the four lanes.
`DD` and `DB=(bank<<1)|1` follow the existing destination parcel convention;
the new probes alone do not sweep arbitrary destinations/banks. The
external fragment backend uses bank zero, even half-indices and source
dwords below 32. Other type widths, rounding modes, NaN/overflow handling
and conversion saturation remain unknown. `encode_convert` emits only
this characterized form; its range checks are software bounds, not a
measurement of the hardware register-file size.

**Inferred correction to register names:** in fragment dataflow, bank-zero
ALU intermediates named `oN` by the old decoder can be read as source `rN`
by a later ALU operation. Apple's multi-instruction sequences motivate
aliasing these scalar intermediate/output names. The external backend
reserves dwords 0–3 for outputs, allocates scratch 4–31 by last use and
rejects pressure beyond that bound. It emits serial instructions and a
conservative `4d00` wait after varying iteration. Corpus/dataflow checks
validate consistency of this model, not physical bank geometry or timing
on the GPU. Do not generalize the alias to all shader stages or the
separate vertex UVSW output convention without evidence.

**Verified scope:** the real-NIR compiler's measurements and unsupported
groups are in round4-report.md (`round4-report.md`, not published). Texture instructions,
predicate state, general compute address operations and memory waits have
not acquired complete encoders this round. The historical byte-confidence
numbers below are unchanged; operation names alone do not make a general
shader compilable.

## ISA round 18: field encoders and evidence

`g5p_usc.py` exposes field-based ALU/move/immediate/conversion/iterator/output/branch
encoders; `g5p_usc_resources.py` adds shared MOV, sample/LOD/read/gather and
bounded buffer-load fields. `decode_instruction()` returns form, fields
and an explicit `unresolved` list, or `None` for unsupported forms.
`encode_decoded()` reconstructs from fields and has no raw-byte fallback.
Source selectors, headers and unknown control bits remain explicit. Range
checks describe representable software fields, not measured GPU limits.

The unchanged Mesa `g5p-gfx` head is `6802a56aa9c`. Its native
`pco_g5p.c` shape gate rejects ALU, and `g5p_pipeline.c` does not call the
older general external backend. The latter already has some fragment ALU,
but rejects vertex ALU and nonconstant unorm8 output. These new instruction
APIs are **not wired into the ICD** and do not make Zink/Hyprland run.
There are no new Mesa commits or patches 0016+ in this round.

### Compact float fields, widths and modifiers

Binary base: `99 HH DD DB AA BB OP FF`; FMA:
`9a HH DD DB AA BB CC 01 OP FF`. AA/BB/CC selectors count halves;
their high group bits and HH are retained without universal bank names.
The final FF parcel continues when an absolute-value extension follows.

| Field | Binary | FMA | New instruction witnesses |
|---|---|---|---:|
| Core opcode | b6=`44` add, `48` mul | b8=`4c` | 89 add / 72 mul / 89 FMA |
| Saturation | opcode bit1 | opcode bit1 (`4e`) | 24 binary / 8 FMA |
| Negate sources | b7[7]=A, [6]=B | b9[7:5]=A/B/C | 8 A / 36 B binary; 8 per FMA source |
| Absolute sources | append `00 MM`, MM[7]=A,[6]=B | MM[7:5]=A/B/C | 20 A / 12 B binary; 8 per FMA source |
| Narrow result | class `89`, b7 bit1 | class `8a`, b9 bit1 | 24 binary / 12 FMA |
| All sources narrowed | b7 mask `0c` | b9 mask `1c` | 16 binary / 4 FMA |
| Alternate compact source layout | class `9d` | class `9e` | 23 binary / 16 FMA; header/group ABI unresolved |

**Correction:** class `9d` does not by itself mean compare. Known opcode,
length and controls distinguish these ALU forms from the predicate forms
above. The tested source/class variants match source-level symbolic flow;
their physical register-bank interpretation is still inferred. Mixed-width
sources and arbitrary combinations of width/modifier bits are not independently
swept. The counts are instances in unique manifest shaders, not individual
orthogonal bit sweeps and not runtime measurements.

Of 130 new scalar/vector float shaders, **109 match symbolic source flow
and 21 are unliftable**. Every instruction in a lifted shader is accounted
for; unsupported ones are not skipped. The remaining shaders are standalone
abs/neg/saturate lowerings and float min/max involving unresolved controls.
The comparison assumes the previously inferred fragment register aliasing
and finite-value normalization; it does not prove NaN/signed-zero/denormal
behavior or instruction hazards. Native source abs/neg modifiers can be
emitted on decoded arithmetic even though those standalone lowerings remain
unresolved. Legacy XOR/AND sign-bit lowerings remain separate alternatives.

### Literal sources, integer operations and conversions

**Verified:** source-A integer literals reuse the entire earlier split
`IMM_MAP`, with b5 holding source B rather than a fixed `01`. The literal
prefix is 8 or 10 bytes; append `OP 01 70 00` for the characterized integer
form. A second **32-bit single-bit sweep plus three mixed values** checks
140 literal XOR instructions; all-ones XOR optimizes to four NOTs. The
36 source-parameter checks distinguish that optimization explicitly.

Float add/mul use a short binary16 literal prefix and tail `OP 09 70 00`,
promoting the literal for float arithmetic (58 new instances). FMA appends
`CC 01 4c 09 70 00` after the short literal prefix (22 instances). Raw
literal bits, not Python floats, are encoder inputs. A general f32 literal
type in these ALU slots is not established; arbitrary u32 constants can
still be materialized by the independently swept MOVI encoder.

| Integer form | Measured fields | Evidence |
|---|---|---|
| Min/max | `99/9d HH DD DB AA BB OP 21 08 00`; OP=`b2/b3/ba/bb` = unsigned min/max, signed min/max | 16 new lane probes |
| Shift left / logical right | `9a HH DD DB 01 VV 20 01 SS 01 f0/f4 04` | 104 dynamic/immediate shift instructions total |
| Immediate unsigned shift | Replace `SS 01` with `(amount<<1)\|1 01 20 01`; final control=`14` | 23 vector source-amount checks |
| Arithmetic right | Compact binary OP=`fc`; literal amount goes in A, value in B in extended literal form | 4 compact instances + 9 vector literal checks |
| f32↔i32/u32 | Earlier TAG/OP conversion form unchanged | 16 new vector conversion instructions |

Dynamic shift callers must mask the amount to31 as in the probes. Eight
shift probes optimize to other instructions and supply no direct shift
encoding evidence. General integer division, carry, 64-bit arithmetic,
exceptional conversion behavior and other widths remain unimplemented.
Unpredicated class-98 reciprocal/rsqrt are **not** exposed by the new API:
their captured lowerings use class9c/predication and extra fixups. Unary
negation is measured only on floor, not every SFU operation.

### Shared sources, buffers, outputs and backward branches

Shared MOV: `98 01 DD 01 RR 01 XX 41 54 01 38 00` with
`RR=81|((shared_dword&31)<<2)` and `XX=(shared_dword>>5)<<1`.
Five MRT probes provide **96 direct mappings** from shared dword
`2+DD/2` to destination half DD; source2…33 and destination0…62 even are
witnessed. The full resource corpus contains 161 shared MOV instances.
Other destination banks and physical common-store geometry remain unknown.

Fixed constant-buffer offsets change metadata kind5 upload source offsets,
often leaving USC byte-identical. Device-buffer fixed-index loads use
class12; dynamic constant/device buffers use class10 after unresolved
pointer construction. **42 fixed + 12 addressed load instructions** are
reconstructed. In class12, `b1&fe=22/3a` means 4/16 bytes, absent operands
default to zero, b2 is destination half, `b3>>1` the common pointer dword,
and optional b4 the element index. In class10's two/four-byte forms,
`b3>>1` is the address half index. Pointer bases common0/common2 are
witnessed; general addresses, cache attributes, stores and waits are not
solved. Size1/2/8 load codes are inferred across stages from earlier compute
evidence and are not claimed as verified fragment support.

Twelve format probes check packed two-word output; nine computing colors
have four narrowed ALU results. Output `1c N1 SS 08` selects source half
SS and N words. BGRA8, RGBA8 and RGBA16F share the characterized packing;
PBE format handling is separate. **Nine blend-state probes** hold the
Metal return expression fixed: off/replace are 12 bytes, premultiplied alpha
124, straight alpha 110, additive/multiply 104, min/max 162 and subtract 144.
The seven destination-dependent cases insert `1c 21 04 28` before ALU and
finish with `1c 21 00 08`. This is evidence for a tile-read association,
not a solved blend epilogue. No general blend encoder is supplied.

Local backward branch `01 01 O0 O1 ff fe` sign-extends the earlier low16
delta: `d1 ff`=-48, `dd ff`=-36. Five old compute loops and five new
fragment loops target the natural loop body boundaries. With forward
branches, **all 1688 manifest/historical targets** pass (15 new manifest
branches +1673 old; repeated counterpart shaders excluded). Mapping the
upper parcels into a full signed32 displacement remains **inferred**:
only zero/sign-extension high bits have been observed. Predicate source,
C8 mask-stack/reconvergence, conditional backward branches and large
offsets remain unknown; the encoder refuses uncharacterized negative
conditional branches.

`assemble_depth_remap_vs()` authors the two ALU instructions for
`p.z=(p.z+p.w)*.5` plus vertex outputs, branch and padding. Its complete
70 bytes equal the newly compiled `v_depth`. Header31/11 and source groups
come from the vertex probe; fragment aliasing is not extended to VS.

### Remaining unknown instructions and integration work

- Float min/max and compare/select predicate sequences (`9d … 58`,
  fused class9a compare/select, `54/55` move controls, `f8/f9/fc` writes).
  Relations are partially associated; predicate identity, NaNs, liveness
  and reconvergence are not known well enough for a complete encoder.
- C8 execution-mask/synchronization, class81 loop tests, class09 iterator
  variants, general class14 argument loads, long 88/89/8a/8d/9e address/carry
  forms, indexed/shared ALU sources beyond the measured literal/compact/MOV
  cases, spills and source-bank geometry.
- Complete texture/sampler word packing and API-binding/common-store/PDS
  ABI, texture wait/control flags, gradients, channels, shadow, arrays,
  offsets, gather order, dynamic UBO/SSBO address arithmetic and memory
  ordering. Known-selector encoders do not solve those runtime dependencies.
- Tile reads and remaining half-ALU blend formats (`89/8a/8d/8e`), alpha/color
  masks, sRGB, depth/MRT behavior and PBE interactions. The MRT probes measure
  fields only and do not enable MRT in the ICD.
- Native NIR lowering and graphics integration at `pco_g5p.c`/
  `g5p_pipeline.c`, scheduling and device numerical qualification. The
  limited decoder is not an ISA validator for arbitrary shader submission.

Stock Rogue is a comparison vocabulary, not an encoder substitute. Its
grouped framing, float opcodes/saturation, shared-bank numbering, contiguous
MOVI, image width/height/address fields and 16-byte sampler differ from G5P.
The existing G5P 8-byte sampler and image/common uploads are capture-backed;
copying stock `texstate.xml` or PCO instruction packers would not close them.

### Round-18 reproduction and artifacts

```sh
bash scripts/gpu-userspace/check-isa-r18.sh             # compile + all checks, offline
bash scripts/gpu-userspace/check-isa-r18.sh --existing  # reuse compiler archives
bash scripts/gpu-userspace/check-isa-r18.sh --unit-only # portable; no Xcode/corpus
python3 -B tools/gpu-userspace/g5p_usc.py SHADER.text.bin
```

Generated source, manifests, GPU binaries and provenance stay in ignored
`build/gpu-userspace/isa-r18{,-resources}/`. Core `validation.json` records
**2342 field reconstructions**, source/modifier checks, per-field counts,
all symbolic failures/unliftable shaders and source/archive/shader hashes.
Resource `evidence.json` includes metadata offsets/bytes/mappings and hashes;
its `validation.json` records **883 checks**, including 380 instruction
reconstructions, source-parameter/mapping checks and freshness checks.
There are **18 portable unit groups**. The original 1566-object ISA checks,
50 MOVI bit cases and own-shader encoder regressions also pass. These are
offline checks; no shader execution or Mesa feature enablement is claimed.
The iOS request (`isa-r18-device-request.md`, not published) specifies the next captures.

## ISA round 2: follow-up to Mesa round 18

This is the requested second ISA investigation after the field encoders in
ISA-r18 and Mesa patches0016–0019. The implementation changes are in
`g5p_usc.py`, `g5p_usc_resources.py` and their offline generators/checkers.
Mesa integration is separate. No workspace commit or device access was made.

### Evidence and reproduction

```sh
bash scripts/gpu-userspace/check-isa-round2.sh             # compile fast + strict and verify
bash scripts/gpu-userspace/check-isa-round2.sh --existing  # require matching compile attestations
bash scripts/gpu-userspace/check-isa-round2.sh --unit-only # portable goldens/framing checks
bash scripts/gpu-userspace/isa-round2-driver.sh            # optional offline, read-only Ghidra evidence
```

The reference corpus index (`../../tools/gpu-userspace/shaders/isa-round2/README.md`, not published)
maps the six requested priorities to named shaders. `gen_isa_round2.py`
generates **509 source functions / render descriptors**, including57 VS
and452 FS functions. Each archive has1018 stage objects; the repeated
counterpart stage is excluded from evidence counts. Both runs use
`air64-apple-ios16.0`, `applegpu_g5p`, Apple Metal32023.921. Strict additionally
passes **`-fno-fast-math`** to the AIR compiler. **347/509** named code streams
are identical between modes; treating the other mode as an equivalent oracle
would lose important NaN and contraction distinctions.

`build/gpu-userspace/isa-round2/{,strict/}` contains `round2.metal`, pipeline
JSON, manifest, AIR metallib, G5P archive, split objects, `compilation.json`,
`evidence.json` and `validation.json`. Attestations bind source, manifest,
pipeline, AIR and GPU archive hashes to commands, compiler identities and math
mode; stale sources/archives are rejected. Evidence includes complete section
bytes/offsets/hashes, common-store mappings, every instruction's decoded fields
or unresolved raw bytes, and per-shader coverage. These artifacts are ignored.

The checker separately verifies source-to-register/output mappings, matrix
expressions, comparison relations/selected values, boolean selections,
computed coordinates, constexpr sampler parameters, branch boundaries and
the authored blend programs. Field round-trips alone do not establish an
instruction's numerical meaning. The final counts are recorded below and in
the machine-readable reports; padding is included in instruction counts.

| Current round-2 checks | Fast | Strict |
|---|---:|---:|
| Named source/stage probes |509 |509 |
| Field instruction reconstructions |6537 |11238 |
| All source/metadata/field assertions |7592 |12587 |
| Shaders with every instruction reconstructed |472 |408 |
| Shaders retaining unknown instructions |37 |101 |
| Unknown instruction instances |190 |2612 |

Both modes pass, for **20,179 assertions /17,775 field reconstructions**.
There are20 new portable test groups; the18 ISA-r18 groups, original1566-object
checks,50 immediate sweep cases and own-shader regressions also pass.
“Reconstructed” still includes explicit unresolved controls, alignment and
repeated source expressions with distinct names; it is not an ISA semantic
coverage percentage or a count of executed shaders.

### 1. Vertex uniforms and the VS/FS upload distinction

**Verified compiler fields:** `encode_mov_shared(..., bank=1)` is the VS
temp-destination form, `98 01 DD 03 RR 01 XX 41 54 01 38 00`. Bank0 (`DD 01`)
also occurs in VS when outputs use the staging bank. The common source map is
the same as FS: `RR=81|((s&31)<<2)`, `XX=(s>>5)<<1`, dword indices. Direct MOV
probes witness sources2…33, both destination banks and the high source bit.
The MOV encoder retains its six-bit source bound; it is not automatically
widened from the separate inline ALU source evidence.

**Verified source flow:** 49 direct-uniform functions check192 common-source
to output-slot mappings, including16 lane/position-slot combinations and VS
exports of2/4/8 uniform vectors. A lane access `c[0][L]` uploads a prefix of
`L+1` dwords from source0 and reads common`2+L`; it does not compact every lane
to common2. Fixed `float4` array offsets0/1/2/3/7/15/16/31/32/63 instead move the
kind5 metadata source offset and keep the four USC common sources2…5.
Bindings1/2/3/7/15 vary metadata independently of the USC source numbers.

Inline source descriptor `S(s,g)` has parcels
`[1|(g<<7)|((s&31)<<2), 01, ((s>>5)&7)<<1, 41]`.
Here `g` is an explicit group/control bit, **not a universal physical bank**.
The32-live-vec4 families expose common128/129; eight index-bit positions are
therefore represented in the inline APIs (software range0…255, not a measured
common-store capacity). Both group values occur in the matrix probes.

| Inline form | Encoding / API | Evidence in the fast corpus |
|---|---|---:|
| Shared A + compact B | `99/9d HH DD DB SA BB SX 41 OP FF [route]`; OP44/48 | `binary.shared`:340 instructions |
| Shared A + compact B/C FMA | A extension before compact C; OP4c | `fma.shared_a`:49 |
| Shared A/C + compact B FMA | A extension, four-byte C descriptor, OP4c | `fma.shared_ac`:51 |
| Two shared source add/mul | class9a, two full `S` descriptors, OP44/48, routing10 | `binary.shared_pair`:112 |
| Destination bank/control | DB[1]=bank0/1; DB[7] preserved separately | Source-checked VS and FS ALU; control meaning incomplete |
| Encoded source routing | FMA70/1c/58 and binary70 are explicit parcels | Different source-order probes; no universal routing synthesis |

**Verified bounded symbolic model:** all18 uniform add/multiply/FMA shaders
and all three `v_mat3_copy/mul/fma` shaders match their source expressions
through output writes (**21 whole-program source-flow checks**). Mat3 copy/mul
are262 bytes and the FMA-color variant326 bytes. The probes use MSL `float3x3` columns at byte0/16/32 and `float4
color` at byte48; padding dwords are real buffer layout, not extra matrix
elements. Kind5 maps16 dwords to common2…17. `v_mat3_copy` computes
`q=proj*float3(p.xy,1)`, writes `(q.xy,0,1)` and exports color, original p and q.
All its instructions reconstruct from fields. The proof uses the measured
VS register convention; it does not assign global meaning to every header,
source group or destination-control combination.

**Upload path, offline driver evidence:** `EncodeShaderConstantsPDSProgram`
at `AGXMetalA9:0x207d1e010` builds the same DOUTD format for both stages.
The old texture capture independently fixes sources/controls: descriptor8
dwords to common0 and sampler2 dwords to common8, controls`10000008` and
`90010002`, program`f0001000 f4001808 d3000000`. Count is in dwords,
destination is shifted by13, `0x10000000` is retained cache/control state,
bit31 marks the last DMA. Last DOUTD sets instruction bit26, then HALT;
there is no USC launch in this loader. PDS constants contain source-address
qwords followed by control qwords. Fixed UBO source offsets belong in these
addresses, independently of common-store destinations and API bindings.

| Stage | Where the loader is scheduled / allocation |
|---|---|
| VS | VDM PDS_STATE_UPDATE on the vertex data master, before the VS launch/fetch program. The historical `20040001` word decodes as common-size4×64B and PDS-data1×16B; these are captured values, not a mat3 size formula. VDM_STATE5 also carries VS common allocation in64B units. |
| FS | PPP fragment state selects the uniform loader; UNIFORMDATABASE is the per-draw ptr3 path. SIZEINFO1 describes uniform PDS data in4-dword units; SIZEINFO2 describes shared allocation in16-dword units. |

The driver paths are separate:
`emitVertexProgramVariantAndArguments` at`0x207d74824` emits VDM state and
VS argument data; `emitFragmentProgramVariantAndArguments` at`0x207d74be0`
emits PPP/UNIFORMDATABASE state. Both call the argument-data emitter, with
different per-stage tables/allocators. A VS loader must not be installed as
the FS ptr3 loader. A new **controlled matrix/UBO runtime upload capture is
still missing**. Dynamic per-vertex UBO addresses, full dependency scheduling
and physical bank geometry remain unresolved. NIR descriptor/offset `iadd`
integration is not established by a shared-register MOV alone.

### 2. Relations, selections, control flow and discard

Measured compare/write form:
`9a/9e HH DD DB AA BB CC 01 OP 60 [00 10]`, where the optional extension
changes byte9 to61. A/B/C compact selectors and header/class/destination
controls remain explicit. It is followed immediately by inverse-predicated
MOV `f8/fc HH DD DB SS 01 54 01 38 00`.

| Primitive association | OP |
|---|---|
| float equal / less / greater |55 /59 /5d |
| 32-bit integer equal (signed or unsigned) |bd |
| signed less / greater |be /bf |
| unsigned less / greater |b6 /b7 |

**Verified source/byte pattern:**72 scalar select functions per math mode
check A/B against the two source lanes and C/inverse-MOV against the selected
values. Another72 functions materialize boolean masks;18 vector functions
select four independent components. There are146 compare/write and187
inverse-move instruction instances in the fast corpus, including nested/math
uses. These counts are instruction witnesses, not tests of arbitrary PREGs.

`assemble_compare_select` defaults to the **strict compiler pattern**:
float ge/le use base less/greater with relation-control`10`; float ne swaps
the true/false values of equality. Fast math instead swaps values for ge/le
and uses control10 for ne. Signed/unsigned ge/le/ne use value swapping.
The control must not be described as a universal logical NOT: the strict
and fast differences specifically expose unordered/NaN handling. Runtime
NaN payload/signaling behavior and physical predicate state are unqualified.

**bcsel:** four uint-nonzero probes and16 selected components establish a
second path. `encode_bool_to_half` encodes the compiler's zero/one f16
condition with literalA=0, literalC=`3c00`, OPb9/control72/routing2070 and
destination-control80. `encode_cond_select` then uses three compact sources,
OP54/control04/routing2000 (`…54 05 00 20`), selecting the observed true/false
values. Its condition contract is the output of that normalization, not an
arbitrary NIR mask bit pattern. Header/group/lifetime controls still matter.

Two small nested if/phi expressions fully lower to these selections.
Texture-sided if/else and the three non-unrolled loops preserve class81/C8
control operations. All **65 fast +481 strict decoded branch targets** are
instruction boundaries (three backward branches in each run). C8 mask push,
else/reconvergence/pop, loop tests and phi copies remain unresolved; a branch
displacement encoder does not make these loops compilable.

**Discard:** `04 68` is emitted for unconditional Metal discard. Conditional
forms use `04 61 SS 00` after a test; six fast probes isolate OP56/5a/5e
with control42 and optional`00 10`. `encode_discard_test` and `encode_discard`
preserve that sequence. Strict tests introduce further controls, including
uncharacterized62 variants. **NIR demote/helper-lane equivalence is unknown**;
Metal discard bytes alone do not qualify derivative behavior or ISP
punch-through state.

### 3. Float math and complete reference lowerings

The corpus has four scalar-lane and one vector variant for each of20 math
expressions, plus five fast-namespace variants. **95 functions in19 fast
families have every instruction reconstructed from fields**. General Metal
`pow(x,y)` is the remaining fast family with unknown instructions. This is
field coverage of compiler lowerings; numerical SFU accuracy, exception
behavior and arbitrary operand/control combinations remain unmeasured.

| Expression | New measured fields / lowering |
|---|---|
| fmin/fmax, finite fast pattern | `9d …58 41/81 08 00`, merge`…55 61 38 00`, inverse MOV.16 tests +16 merges. `assemble_float_minmax_finite` authors this sequence. Strict min/max uses different NaN fixups, still incomplete. |
| reciprocal | Core80 plus literal-FMA routing58, two-source FMA routing0c and literal compare fixup. The196-byte vector stream fully reconstructs. Core80 alone is not the full operation. |
| rsqrt | Literal bit-pattern equality test for negative zero with negative-infinity result, followed by classf8 core81. The116-byte vector stream fully reconstructs. |
| sqrt | Core81 plus literal test/fixup and multiply;140-byte vector stream. |
| sin/cos | Literal range reduction, floor, cores82/86 and multiply;308/292-byte vector streams. Strict versions have extensive additional range-reduction/control-flow paths, still incomplete. |
| exp2/log2 | Unary85/84; absolute-input log2 extension also measured in pow lowering.44-byte vector streams. |
| sign | Two literal/compare instructions per lane;156-byte vector stream. |
| floor/fract/ceil | Unary40; fract subtracts floor; ceil uses floor of negated input and literal subtraction.44/76/92-byte vector streams. |
| round/rint/trunc | Literal/select fixups plus f32→i32→f32. TAG17/OP60 associates with rint's integer rounding; TAG13/OP60 with truncation.332/156/156-byte vector streams. |
| pow(x,2), pow(x,.5) | Single-source square routing30; abs-rsqrt/test/multiply specialization, respectively.52/156-byte vector streams. |
| positive-domain pow lowering | Explicit `exp2(log2(x)*y)` is112 bytes for float4 and fully reconstructed. General Metal pow adds parity/sign/exception handling; its440-byte stream retains eight unknown instructions. |

`encode_literal_ternary` exposes the measured split literal A, compact B,
compact or split literal C, opcode, operation-control byte and optional routing
parcel. Only observed opcode/control/routing tuples are accepted. There are323 instances in the fast corpus. These are **encoded slot
names**, not a universal `A*B+C` or compare ABI. C-literal bit9 is unswept and
the encoder refuses constants requiring it. Native source-class extensions,
destination-control80, full-f32 ALU literals and source-negated literal ALU
are now preserved rather than silently dropped. `encode_sfu_core` explicitly
names approximation/range-reduced cores; complete reference context is kept
in each named shader. Header/group and numerical unknowns remain in decoded
records even when their fields reconstruct exactly.

### 4. Fragment coordinates, arrays and computed texture coordinates

| Form | Encoding and measured association | Fast witnesses |
|---|---|---:|
| Raster XY | `1c N1 DD 68 [source extension]`; y-only uses`…69 02 00` |7 instructions across6 position probes |
| XY conversion | u2f32 then multiply by f16`2c00` (=1/16) | Scalar and vector position probes |
| Position w/z | Special iterator with b1 bit1 clear: `08 08`=w into half0; `08 09 DD 02`=z; `08 11 DD 00`=w,z |4 instructions |
| Computed sample coordinates | Existing sample coordinate base selects the temp values produced by ALU; no new sample opcode |21 sample functions |
| Projective iterator control | `08 13 DD SS 03 80` versus tail`00 80` | One `a.xy/a.w` expression in three target formats |

The XY scaling is established by compiler dataflow; pixel origin, per-sample
locations and GL/Vulkan viewport conventions still require runtime checks.
Using position.z also changes user-varying slot allocation. Never assign a
user varying from an assumed fixed slot after introducing a position input.

All21 texture-coordinate functions reconstruct, including add/multiply/FMA,
uniform scale/bias, position and projective coordinates for RGBA32Float,
BGRA8 and RGBA8 targets. Nine simple ALU functions additionally check18
symbolic coordinate components against the source. Their texture instruction
is identical to that format's direct-varying sample, with coordinate temp
base0. The narrow result class18 is therefore not restricted to the original
iterator-fed device capture. Projection-control3's complete interpolation
semantics and general perspective metadata are still unknown.

Eight local-array programs cover2/4/8/16 elements with dynamic reads and
optional writes. Their compiler streams use unrolled arithmetic/select
lowerings, not a demonstrated indexed-temp or scratch-memory ABI. Predicate
ALU variants in them remain unknown. They are reference inputs for a bounded
NIR-to-select lowering, not evidence for spills or indirect register access.

### 5. Sampler state and image formats

**Important schema distinction:** all48 constexpr sampler functions have
byte-identical USC. State is in `__GPU_LD_MD`: root.field0.field3 is the
constant-sampler table vector; field4 is its packed-u64 vector. Per-sampler
field12 repeats that packed value. `compiler_sampler_metadata` verifies both
copies and exposes exact offsets/API enums. This host compiler's default
packed qword is `807bff0000080049`; it is **not** the captured runtime G5P
sampler `00c00780000e0000`.

The48 source probes independently change min/mag nearest/linear, mip
none/nearest/linear and address clamp/repeat/mirror/zero. The separate
`encode_compiler_sampler` reproduces their host metadata, including min bit11,
mag bit9, mip bits14:13 and three3-bit address groups at0/3/6. These bit positions
must not be used for the iOS sampler upload.

**Driver-derived G5P runtime fields:** the offline iOS16.7.10 AGXMetalA9
`SamplerDescriptor(ConstantSampler*)` at`0x207dd05e0` and
`SamplerStateFields` at`0x207d80720` map API fields into the following qword.
`initWithDevice…` at`0x207d80588` and `registerConstantSamplerList` at
`0x207cf4310` apply additional QOS masks. The default reproduces the earlier
nearest/clamp capture exactly. New combinations have **static driver evidence,
not new device captures**.

| Runtime bit field | Encoding |
|---|---|
| min / max LOD clamp | [9:0] /[19:10], units1/64; driver clamps to0…896 (=14 levels) |
| mag / min linear | bit23 /bit25; nearest=0 |
| mip mode | [28:27]:0 none,1 nearest,2 linear |
| wrap U /V /W | [31:29] /[34:32] /[37:35] |
| wrap values (normalized coordinates) |0 clamp-to-edge,1 repeat,2 mirrored-repeat,3 clamp-to-zero |
| other bits | Explicit opaque value; captured default`00c0078000000000` includes comparison/QOS controls |

`encode_sampler_state` and `decode_sampler_state` implement this normalized,
non-anisotropic subset. Unnormalized/filter2/anisotropic modes are rejected.
Predicted dwords: linear/clamp without mips=`028e0000 00c00780`;
linear/repeat on all axes with linear mip filtering=`328e0000 00c00789`.
The latter two still need controlled captures and numerical images before
being called device-qualified sampler states.

BGRA8 and RGBA8 **render targets** appear in all coordinate/blend comparisons.
This does not specialize a Metal `texture2d<float>` input to either image
format. The existing runtime image descriptor is still the captured4×4
BGRA8 case. General sampled RGBA8/R8 descriptors, mip addresses/strides,
channel swizzles and image-format/PBE interactions remain capture requests.
The R8 blend descriptors measure output lowering only.

### 6. Shader-based blending

Changing blend state inserts mode28 tile reads and arithmetic in the FS.
Twenty additional explicit `[[color(0)]]` framebuffer-fetch functions provide
named destination operands independently of the blend attachment metadata.
For BGRA8/RGBA8 and RGBA32Float, the entire premultiplied epilogue from tile
read onward equals `src + dst*(1-src.a)`'s explicit-fetch epilogue. The
narrow input-iterator control differs for the normalized blend source and
the explicit-fetch input; it is preserved by the reference assembler.

| New form | Fields / bound | Fast witnesses |
|---|---|---:|
| Tile read | `1c N1 DD 28`, N=1/2/4 output words |38 |
| Packed tile component | literal/selector form ending`28 81 4d 00`; source/destination half indices explicit |76 |
| Native half add/mul | class89/8d OP04/08; source controls40/80 explicit |24 |
| Native half FMA | class8a/8e OP0c, source-control80 |30 |
| Native half one-minus | Literal1 minus source with the measured group/header/routing |14 |

`assemble_premult_blend_fs()` authors **124 bytes** for either BGRA8 or RGBA8
and **68 bytes** for RGBA32Float, byte-identical to Apple's named blend
programs. It uses field encoders, not a raw shader-byte template. It is a
standalone varying-slot1 reference with fixed scratch/source conventions,
not a general epilogue allocator. The half tile-component instruction's
normalization/clamp semantics and mixed-source controls remain inferred.
Straight-alpha shaders are in the corpus; packed half fused-product controls
and several R8/RGBA16F paths remain uncharacterized.

The evidence establishes a **shader-based** color-blend path for these
pipelines. No blend-factor word is identified in PBE/EOT. Live ISP ordering,
coverage/interlock, alpha/color masks and the PBE/EOT state for such a draw
remain unqualified. The next capture must retain both shader and those states;
reusing an unrelated solid-color EOT is not evidence that blending works.

### Precise remaining unknowns and handoff

The machine-readable coverage lists every unsupported instruction by named
shader and byte offset. The fast unknowns are concentrated in structured
control flow/local arrays, general Metal pow, and the remaining blend/fetch
formats. Strict adds substantial min/max/NaN, sin/cos range reduction and
uncontracted/control-flow paths. The following are still requirements:

- General class81/C8 predicate identities, push/else/pop/reconvergence,
  loop/phi semantics, predicated arithmetic and inactive-lane behavior.
  Simple selection recipes do not define the whole execution-mask machine.
- Metal discard versus Vulkan demote/helper semantics and ISP punch-through
  state; strict discard's additional test controls.
- Physical source-bank/header/destination-control ABI, register reuse across
  unrelated instruction families, scheduling/waits and independent arbitrary
  shader allocation. Encoding reconstructed fields is not a validator.
- Full strict min/max, general Metal pow's integer/parity and predicated-fixup
  forms, strict sin/cos slow paths, SFU numerical accuracy, NaNs, signed zeros,
  subnormals and conversion overflow. Positive-domain pow lowering is separate.
- Indexed temps, spills/scratch ABI and complete predicated array lowerings;
  arbitrary per-vertex/dynamic UBO pointer arithmetic and memory ordering.
- A controlled VS matrix/UBO PDS/data-master/common-store capture. The new
  common operands close the ISA encoding gap; execution and upload integration
  are distinct from compiler archive agreement.
- New sampler words, RGBA8/R8 sampled-image and mip layouts, projective
  iterator semantics, derivative/gradient and resource/hazard modes.
- Blended-draw ISP/coverage/interlock and PBE/EOT state, straight-alpha packed
  controls, other target formats and numerical blend qualification.

The round-2 iOS request (`isa-round2-device-request.md`, not published) gives exact names,
descriptors, inputs, staged acceptance and artifacts. It has **not been run**.
The historical iOS/host agreement for earlier shaders does not qualify any
new instruction or combination in this round.

## Historical coverage and reproduction

**Verified, same round-2 corpus (1,566 objects, 1,071 manifest functions):**

| Byte confidence | Before | After |
|---|---:|---:|
| Verified tested fields / known classes | 179,774 (61.7%) | 180,258 (61.9%) |
| Inferred / classified | 73,644 (25.3%) | 91,280 (31.3%) |
| Unknown | 37,848 (13.0%) | 19,728 (6.8%) |

**Verified metric limits:** these are bytes attributed to an instruction's
confidence, **including padding, repeated vertex objects and opcode-only
classifications** inherited from round 2. They are not a bit-coverage or
semantic-completeness percentage. The new `bytes_excluding_padding` report
is 57.0% verified, 35.4% inferred, 7.6% unknown on that corpus. Unknown bytes
mostly became inferred address operations; no “fully solved” claim follows.
The new 61-function / 97-object probe set is 75.6% verified, 19.0% inferred,
5.4% unknown including padding. Report the unchanged corpus for comparisons.

**Verified commands** (no device; this round's new outputs are in ignored
`third_party/`, respecting the task's write scope):

```sh
python3 -B tools/gpu-userspace/g5p_isa.py verify \
  build/gpu-userspace/offline/r2c/split_g5p build/gpu-userspace/offline/r2c/manifest.json
python3 -B tools/gpu-userspace/g5p_isa.py verify \
  build/gpu-userspace/offline/vtx/split_g5p build/gpu-userspace/offline/vtx/manifest.json
python3 -B tools/gpu-userspace/gen_round3.py third_party/g5p-round3
bash tools/gpu-userspace/offline-compile.sh third_party/g5p-round3/round3.metal \
  third_party/g5p-round3/round3.mtlp-json third_party/g5p-round3
python3 -B tools/gpu-userspace/g5p_isa.py verify \
  third_party/g5p-round3/split_g5p third_party/g5p-round3/manifest.json
python3 -B tools/gpu-userspace/gen_variants.py third_party/g5p-round3/bits bits
bash tools/gpu-userspace/offline-compile.sh third_party/g5p-round3/bits/bits.metal \
  third_party/g5p-round3/bits/bits.mtlp-json third_party/g5p-round3/bits
python3 -B tools/gpu-userspace/g5p_isa.py selftest third_party/g5p-round3/bits/split_g5p
python3 -B tools/gpu-userspace/g5p_isa.py encoder-tests \
  build/gpu-userspace/offline/var/split_g5p build/gpu-userspace/offline/split \
  build/gpu-userspace/device-analysis/split-air
```

**Verified outputs:** all listed checks pass with zero failures; round-3
JSON is under `third_party/g5p-round3/`. **Inferred priority:** resolve full
compute pointers/masks, texture descriptors, scheduling/waits and predicate
state before general shader compilation. Sparse captures, not decoder
labels, determine whether a shader can be safely replayed.
