# FWTRACE: reviewed encoding candidate, deployment blocked

This is an **offline candidate, not an approved firmware patch**. Its six sites,
original bytes, branch encodings, displaced instructions and stubs have been
checked against the extracted C0 LDR, the local MINIM decoder, the META HTP
instruction manual and GNU metag binutils 2.30. The required proof that the
proposed **local executable extension is unused at runtime is missing**. The
verifier's `--deployment-check` therefore exits 2 even on an otherwise exact
match. The kernel agent must not apply this candidate as an authorized image
variant. No firmware image, kernel source, device or branch was modified.

This corrects the earlier round-10 experiment #4: suggesting shared-RAM markers
was not yet a complete patch contract. In particular, a zero-filled image gap
is not evidence of free live RAM. I cannot honestly supply the requested proof
that nothing in firmware uses the proposed local range: the decoder has UNKNOWN
instructions and indirect accesses are unresolved. No reservation below should
be inferred from the firmware merely lacking an LDR record there.

## Files and commands

- `scripts/gpu/firmware-re/fwtrace-spec.json`: exact candidate bytes, addresses,
  per-instruction standard META words, register/flag and reservation contracts.
- `scripts/gpu/firmware-re/fwtrace.py`: deterministic offline generator/verifier;
  default mode never writes anything. `--write-spec` writes JSON only.

```sh
python3 scripts/gpu/firmware-re/fwtrace.py
python3 scripts/gpu/firmware-re/fwtrace.py --binutils
python3 scripts/gpu/firmware-re/fwtrace.py --deployment-check
```

The first two commands audit the candidate; they do **not** authorize deployment.
The last deliberately returns 2. Any raw LDR identity mismatch, changed spec,
original-site mismatch, unknown instruction, footprint mismatch, out-of-range
branch or decoder mismatch returns 1. `--binutils` additionally uses the existing
Docker image and Linux META objdump; it matches every disassembled mnemonic,
operand and branch destination, not just whether objdump recognizes the word.
No assembler is assumed: the existing binutils build disables GAS. MINIM bytes
are generated directly using HTP TRM 2.1.235 sections 3.2.8 (branch), 3.2.19
(immediates), 3.2.20 (moves), 3.2.21 (MSET), 3.3 (OR/SET), and the existing long
GETL encodings copied from compiler epilogues. Decoder round-trip and GNU
standard-META expansion are cross-checks, not a hardware execution test.

The verifier walks the **original raw linked LDR records**, validates L1/L2
shapes and bounds, rejects overlaps, and materializes LOAD/ZeroMem destinations.
It never treats a padded reconstructed `.bin` as authority for original bytes.
The exact LDR SHA-256 pin below also covers its original checksum bytes; this
work does not claim to have implemented Apple's LDR checksum algorithm. Core
ZeroMem records remain occupied for the reservation audit even though the Apple
host packer skips copying them. SHA identity is checked before inspecting sites.

Pinned LDR: `build/gpu/extracted/agx-g5p-c0.ldr`, 119112 bytes,
SHA-256 `33ae20cbbe2498e4f119ba4e9a55467e4e0192d4c86d5bf1903adce63ffa5dca`.

## Exact site list

Execution PC and LOAD address are different. For this local MINIM window:
`LOAD = 0x80000000 + (PC - 0x80000000)/2`. An extended instruction consumes
four storage bytes and advances PC by eight. Data pointers never use this
conversion. Packed image offsets refer to the existing `g5p_image_offset()`
core-source segment: `image+0x24000 + LOAD - 0x80000080`.

| Site | Execution PC | LDR LOAD address | Original bytes | Replacement bytes | Stub PC / LOAD | Trace word / marker |
|---|---|---|---|---|---|---|
| policy_enter | `0x8000a4b4` | `0x8000525a` | `01c18285` | `16c06092` | `0x8000b000` / `0x80005800` | `0x43208000` / `1` |
| policy_return | `0x8000a128` | `0x80005094` | `71c1819c` | `1fc0c092` | `0x8000b100` / `0x80005880` | `0x43208008` / `2` |
| ready_result | `0x8000a248` | `0x80005124` | `007402c00491` | `1fc0c091fe93` | `0x8000b200` / `0x80005900` | `0x43208010` / `3` |
| service_enter | `0x8000a444` | `0x80005222` | `01c18285` | `1dc0e091` | `0x8000b300` / `0x80005980` | `0x43208018` / `4` |
| compute_kick_written | `0x80008f3c` | `0x8000479e` | `82b52d4f` | `49c02092` | `0x8000b400` / `0x80005a00` | `0x43208020` / `5` |
| compute_finish_enter | `0x80008cdc` | `0x8000466e` | `05a65d9e` | `50c02091` | `0x8000b500` / `0x80005a80` | `0x43208028` / `6` |

The JSON contains every literal stub byte and every instruction's LOAD address;
there are no symbolic relocations or unfilled branch targets. All six are one
candidate set. Do not combine with the earlier skip-pre-MTS image patch.

- `policy_enter` replaces the MOVT in trampoline `8000a4b4`, replaying it before
  continuing at `8000a4bc`. Both the first pre-MTS call and the retry call use
  this trampoline. It reports wrapper entry, not entry to `1000c0d8`: the wrapper
  can skip the implementation when cached 6c0/6c8 controls are clear.
- `policy_return` is the first pre-MTS call's continuation at `8000a128`.
  It replays `MOV D0.5,TXSTAT`. A marker here proves that first call returned.
  It does not count later retry-call returns at `8000a2a0`.
- `ready_result` replaces `CMP D0Re0,#0; BNE 8000a36c`. Before replaying the CMP,
  it stores the **raw returned D0Re0** at `43208014`, then marker3 at `43208010`.
  The displaced BNE is relocated; false continues at `8000a254`, true at
  `8000a36c`. This observes the full `10003bb4` result, not merely the earlier
  `1000d044` predicate. Value0 is meaningful only with marker3 present.
- `service_enter` replaces the first MOVT of trampoline `8000a444`, replays it,
  and continues to the existing jump into `10002504`. The original call at
  `8000a370` remains in place; its RtP is preserved. This is service entry,
  not proof the DC cmd8 arm ran or consumed a command.
- `compute_kick_written` first executes the displaced `SETD [D0Ar6+#1144],D0FrT`
  (the R0+478 kick store), then writes marker5 and resumes at `80008f44`.
  It proves the firmware issued the original store. It does not prove the device
  accepted it, that478 stays1, or that hardware produced event4.
- `compute_finish_enter` replays the original finish cleanup prologue at
  `80008cdc`, then continues at `80008ce4`. It is after the interpreter's event
  and4a0 wait points; absence alone does not distinguish those waits. Existing
  stream cursor, pending mask and4a0 snapshots remain necessary.

## Stub preservation

Each stub uses the following sequence; the JSON gives its literal encoding and
actual displaced instructions. Marker values are1..6, one sticky word per site.
No counter increment, conditional tracing loop, live META slave read, or call is
introduced. Site records occupy eight bytes each. The second word is unused
except for `ready_result`'s raw return sample.

```asm
; compute_kick_written alone: execute original SETD before the save/marker
MSETL [A0StP++],D0FrT,D0.5  ; save D0.4,D1.4,D0.5,D1.5; SP +=16
MOVT  D0FrT,#0x4320
OR    D0FrT,D0FrT,#site_low16
; ready_result alone: SETD [D0FrT+#4],D0Re0
MOV   D0.5,#marker
SETD  [D0FrT],D0.5
GETL  D0FrT,D1RtP,[A0StP+#-16]
GETL  D0.5,D1.5,[A0StP+#-8]
SUB   A0StP,A0StP,#0x10
; replay displaced position-independent instructions, then B continuation
; ready_result instead: CMP D0Re0,#0; BNE 8000a36c; B 8000a254
```

`D1RtP` is the D1 partner of D0FrT and is explicitly restored. No original CALLR
moves. The marker body changes only saved registers and transient A0StP. MOV,
MOVT, OR without S, address-unit SUB and memory operations do not set condition
flags. The ready CMP executes after restoration; its BNE sees exactly its
result. The finish prologue executes after restoration, leaving the same stack
and saved registers as before instrumentation. Extra temporary stack demand is
16 bytes at eight-byte alignment, **which still needs a stack high-water bound**.
The ABI examples and original compiler epilogues support the save/restore order;
this is not a dynamic stack-overflow test. Tracing adds latency and can perturb
timer/policy behavior even while preserving architectural inputs.

## Reservation proof: what is established and what is missing

**Trace data: allocate a new page, do not borrow an existing word.** Proposed
GPU VA `e1c3208000`, size4000, META uncached root alias **`43208000`**. The current
root allocation is `e1c3200000..e1c3207fff`; the new page begins exactly after it.
Extend its owned coherent-DMA allocation/mapping from8000 to c000. Explicitly
exclude the added page from all object/queue/BO allocation, root pointer tables
and snapshot payload writes; zero it only before firmware release. Retain it
until firmware is parked/quarantined. Host observations use the CPU mapping and
`dma_rmb()` before loads. The existing root META-address helper supplies the
uncached alias; confirm that the expanded mapping receives the same attributes.
No ordinary firmware pointer is changed to point at this page. This provides
host ownership **by construction after implementation**, not evidence that the
page already exists. A dedicated visibility probe is still required before
interpreting an absent marker as control flow. Do not use cached shared aliases
or assume the existing root bounds permit the extra page.

**Code: unresolved local reservation.** Candidate LOAD range
`80005800..80005bff` (execution window `8000b000..8000b7ff`), six80-byte slots.
The original LDR's local payload is `80000080..8000565b`. There is no original
LOAD/ZeroMem record in the candidate range, checked against all94 L1 records.
Private state starts at `8000a000`; stack reservations start at `8000a3c8` and
`8000b3c8`. These are **data/LOAD addresses**, so the stub PC's apparent numeric
overlap with a stack address does not itself mean storage overlap. It also does
not prove a valid physical instruction/data mapping. There is no recovered
linker reservation, exhaustive indirect-store analysis, or RAM-size/fetch proof
for the candidate range. We therefore cannot assert it is free. Merely
observing zero bytes or success on one boot would not prove exclusive ownership.

If that proof is obtained, the exact host copy contract for this candidate is:
write stubs to packed `image+29780` onwards before publication; extend the core
copy source span from55dc to **5b80** bytes, destination still80000080, so its
exclusive end becomes80005c00. `init+48c` must match5b80 and the packer must
explicitly permit the extension. The source remains within the30000-byte image.
Initialize the entire extension deterministically and account for source PTE
attributes and instruction visibility during normal boot. This paragraph is a
**conditional implementation specification**, not permission to use the gap.
Changing the reservation or relocating stubs requires regenerating and reviewing
all branches/spec bytes. The verifier deliberately provides no patch-output mode.

To lift the deployment block, provide an authoritative local-memory layout or
complete supported ownership argument for that range, prove implemented physical
RAM/fetch and16-byte stack headroom, and implement/test the new host page's
exclusive reservation and visibility. Then review a versioned spec and verifier
change together. Editing only `deployment_authorized` in JSON is rejected.
A separately mapped executable allocation with reviewed META segment setup could
avoid relying on a local gap, but would require different trace sites or a
reviewed far-transfer mechanism; none is supplied here.

## Interpretation and verification record

On a fresh boot and a successfully validated trace mapping, proposed log labels:

```text
FWTRACE policy_enter=1 policy_return=0 ready_seen=0 service_enter=0
FWTRACE policy_enter=1 policy_return=1 ready_seen=1 ready_raw=00000000 service_enter=0
FWTRACE policy_enter=1 policy_return=1 ready_seen=1 ready_raw=00000001 service_enter=1
FWTRACE compute_kick_written=1 compute_finish_enter=0
```

These lines are proposed decoding examples, **not observed device output**.
First line localizes the first wrapper's synchronous path only if marker1 is
known to be the first call; sticky words are not counts. Readiness samples can
be overwritten by later iterations and are not an atomic history. A later
ready0 can coexist with service1 from an earlier iteration. Never infer strict
cross-record chronology from one timeout snapshot. Marker5 with marker6 absent
fits the existing event4/4a0 wait evidence but does not select between those two
waits. Numerical completion still requires stamp1, output12345678 and intact
sentinels.

Offline checks performed for this candidate:

- raw original LDR hash/record reconstruction and all six site byte checks;
- every generated MINIM instruction expanded to its independently specified
  standard META word; exact branch ranges, targets and overwritten footprints;
- GNU metag binutils2.30 matched all70 expanded instructions **and operands**;
- a changed raw LDR byte rejected with exit1; a changed replacement spec rejected
  with exit1; deployment check refused the unproved reservation with exit2;
- existing decoder anchor checks passed. No target execution or memory ownership
  certification was performed.
