# IMG lineage: loader yes; stock host protocol not established

## Cheap debug-string check, performed first

**Verified:** exact firmware strings `USC Pipe %u State Overview`,
`META registers`, `RGX_CR_META_SP_MSLVIRQSTATUS`, `T0ENABLE` occur at LDR
offsets `0x17234`, `0x17790`, `0x177ac`, `0x177f8`. The first two strings
are not in AGXG5P's host `__cstring`. These diagnostic headings originated
in the META image, not an identified host `rgxdebug.c` clone.

**Verified:** comparison with three GPL/MIT `rgxdebug.c` drops gives:

| Probe | MediaTek 3.18 | Intel fugu 3.10 | ChromeOS DDK 1.10 |
|---|---|---|---|
| Exact USC heading | absent | absent | absent |
| Exact META heading | absent | absent | absent |
| `RGX_CR_META_SP_MSLVIRQSTATUS` | present, line 2819 | present, line 2455 | present, lines 4172 / 4556 macro form |
| `T0ENABLE` token | absent | absent | present as register macro, line 4739 |

**Verified evidence:** generated `build/gpu/string-comparison.json` records
exact file-byte offsets; the fetch script pins and hashes all three sources.
Primary source pages:
[MediaTek](https://android.googlesource.com/kernel/mediatek/+/4f43e6b499c6d194030df8d2506485db9d5165bd/drivers/staging/imgtec/rogue/rgxdebug.c),
[fugu](https://android.googlesource.com/kernel/x86_64/+/45671fc2c0ffbf7b7ca4ff1264c6ba9cb9a65783/drivers/external_drivers/intel_media/graphics/rgx/services/server/devices/rgx/rgxdebug.c),
[ChromeOS](https://chromium.googlesource.com/chromiumos/third_party/kernel/+/e2e07a89da87b5327149dad416d87c51c2a57b38/drivers/gpu/drm/img-rogue/1.10/rgxdebug.c).

**Inferred:** the register-name overlap is expected for the same hardware
family. The stronger condition in the brief—matching distinctive debug
output proving a DDK-derived host implementation—was **not met** in these
three samples. It is not justified to infer the FWIF from these strings.
This limited negative result does not rule out other DDK ancestry or drops.

## What can be compared directly

**Verified:** the blob's LDR header, linked command records, L2 lengths,
LoadMem/ZeroMem/Config/StartThreads operations match the IMG META loader
definitions. This is a concrete shared binary container, independent of any
host-ring comparison. See [firmware-format.md](firmware-format.md).

**Verified:** host strings in AGXG5P include `AGFIAcceleratorRingEntry`,
`AGFIFirmwareEventRingEntry`, `AGFIChannelErrorEventArgs`, timestamp queues,
per-TA/3D/CL submit methods, and separate event, log, trace and statistics
drain methods. Examples and their string VAs are in `extracted/strings.tsv`.
These are Apple interface names; naming alone does not prove structure
incompatibility.

**Verified structural evidence:** `AGXMetaFirmware::submitDeviceControl`
is identified by assertion references at VA `0xfffffff006ca070c` and calls
the ring-copy helper at **`0xfffffff006c98234`** (`...a0610` callsite).
The helper does the following:

- **Verified instructions:** load control pointer from object `+0x360`,
  read a u32 at control `+0x10`, increment modulo 256, compare against
  control `+0` (`...98254–98268`).
- **Verified instructions:** load ring storage from object `+0x368`, index
  slots with stride **`0x14`**, copy 16+4 bytes (`...98288–982a0`).
- **Verified instructions:** execute `dmb ish`, then publish the incremented
  index at control **`+0x10`** (`...982c8–982e4`).
- **Inferred semantic names, high confidence:** control `+0` is the firmware
  consumer index; `+0x10` is the host producer index. Capacity is 256 slots
  with one slot reserved by the full-ring test. Object member offsets are
  host C++ layout, not offsets in a firmware init structure.

**Verified IMG comparison:** GPL MediaTek `rgx_fwif_km.h:301` has three u32
fields in order write/read/wrap (`0/4/8`); `:533` gives KCCB Kick command
101, MMU cache 102, power 110, etc. Current Linux
`pvr_rogue_fwif.h:805` has write offset `0`, 124 bytes padding, read offset
`0x80`, wrap mask `0x84`, command size `0x88`. Its Kick opcode adds the
Rogue command magic. These differ from the observed Apple control offsets.
Primary GPL header:
[rgx_fwif_km.h](https://android.googlesource.com/kernel/mediatek/+/4f43e6b499c6d194030df8d2506485db9d5165bd/drivers/staging/imgtec/rogue/rgx_fwif_km.h).

**Verified further Apple evidence:** drain/log code at
`0xfffffff006c99f44–99fbc` reads four producer/consumer pairs at offsets
`0/0x10`, `0x20/0x30`, `0x40/0x50`, `0x60/0x70` in an object-referenced
control allocation. **Inferred:** Apple's repeated 16-byte index spacing
is deliberate and extends beyond the one device-control ring. Exact
identities, ownership and entry layouts for each pair remain unresolved.

**Inferred conclusion:** no measured percentage of Apple protocol code
reuse can be stated. **Zero byte-compatible command/init structures have
been established.** One measured control structure differs. This is evidence
against treating Apple's firmware as a drop-in IMG FWIF implementation, not
proof that no lower-level packet or internal helper derives from IMG.

## Era and practical reuse

**Verified:** a contemporary Apple firmware compile date appears at LDR
`0x176c8` (`Jul  2 2024 20:39:36`), while the LDR/container style also
exists in the sampled older GPL sources and current Linux. It contains
Apple `AGFAccelerator` diagnostics and two independently linked thread
images. **Inferred:** a long-lived META toolchain/container lineage is
visible; a particular DDK release, host ABI era, or amount of copied IMG
source cannot be dated from this evidence. “DDK 1.x compatible” would be
an unsupported claim.

| Area | Round-1 evidence | Proposed reuse / status |
|---|---|---|
| META LDR parser | Verified structural match | Inferred: directly useful, with Apple address mapping and startup policy |
| META slave port, thread/register definitions | Verified shared names; public definitions exist | Inferred: strong starting point, verify MMIO window and revision first |
| Rogue MMU / SLC / reset definitions | Verified public Linux implementations | Inferred: compare to Apple accesses before adopting sequences or PTE bits |
| KCCB, FWCCB, OSINIT/SYSINIT layouts | Verified open headers; measured Apple ring differs | Inferred: reference material, not current ABI definitions for this device |
| TA/3D/CDM control streams, PDS, USC compiler | Verified Mesa source exists | Inferred: promising after packet/ISA spot checks; no execution validated |
| Firmware binary | Verified Apple G5P blob extracted locally | Inferred: first G2 target; leave under build and load locally |

**Inferred next decisive static work:** recover the shared init allocation
and ring descriptors from `AGXMetaFirmwareG5` setup, then compare each field
to known FWIF headers with offsets and access directions. Use bounded
emulation of ARM64 host helpers if their input/output structure construction
is easier to observe that way. No iOS dyld shared cache is required for this
next step.
