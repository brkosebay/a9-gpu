# G5P C0 extraction and firmware format

All addresses below are **verified unslid kernelcache VAs**, unless marked
**inferred**. Input hash and reproducible commands are in README.md (`README.md`, not published).
`KC +` means an offset in the decompressed input; `LDR +` means an offset in
`build/gpu/extracted/agx-g5p-c0.ldr`.

## What to extract

| Status | Object | Header VA | Header KC offset | Relevant size |
|---|---|---|---|---|
| Verified, prelink + Mach-O | `com.apple.AGXG5P` | `0xfffffff005940440` | `0x101c440` | `__TEXT` `0x8860`; executable segment `0x431a4` |
| Verified, prelink + Mach-O | `com.apple.AGXFirmwareKextG5P` | `0xfffffff0055d60c0` | `0xcb20c0` | `__TEXT` `0x1d824` |
| Verified, prelink + Mach-O | `com.apple.iokit.IOGPUFamily` | `0xfffffff005938f00` | `0x1014f00` | manifest has every segment |

**Verified:** the prelink dictionary is XML beginning at KC `0x2820000`,
with hexadecimal `<integer>` values and `ID`/`IDREF`. `_PrelinkExecutableSize`
describes the text allocation here, not the whole split-segment executable.
AGXG5P's `__TEXT_EXEC` is at `0xfffffff006c7a000`; its kext-relative fileoff
`0x1339bc0` is not the outer KC offset. The extractor resolves segment VAs
through the outer Mach-O; it does not naively add kext fileoffs to KC offsets.

**Verified:** `AGXFirmwareKextG5P::requestFirmwareBinary` is identified by its
assertion string at VA `0xfffffff0055d673e`, and the method body at
`0xfffffff0064280c0`. At `...80cc` it compares `w3` with `5`; on success it
stores VA `0xfffffff0055d679c` to `[x1]` (`...80d4–80dc`) and length
`0x1d148` to `[x2]` (`...80e0–80e8`). That pointer and length exactly match
the carrier's `__TEXT,__const`, at **KC `0xcb279c`**.

**Inferred:** enum value 5 means Apple's C0 revision, supported by the
`AGXG5P_C0` personality and `g5p_c0_t0.elf`/`t1.elf` comments. It does not
mean Rogue branch 5 or establish any component of BVNC.

**Verified:** raw GPU blob SHA256:
`33ae20cbbe2498e4f119ba4e9a55467e4e0192d4c86d5bf1903adce63ffa5dca`.

**Verified correction:** the literal section name `fwFirmwareImage`, seen
in the overall kernelcache, is in `com.apple.driver.AppleS8000PMPFirmware`:
VA `0xfffffff007a75000`, size `0x1b620` (112,160 bytes). The manifest records
this independently from that kext's own section commands. It is not emitted
as GPU firmware. Seven KPI pseudokexts are explicitly listed as skipped in
the ownership scan; the GPU and PMP headers themselves parse successfully.

## Container

**Verified:** the GPU blob decodes without decompression as the little-endian
META LDR structures in Linux `pvr_rogue_meta.h:139` and
`pvr_fw_meta.c:process_ldr_command_stream`, and in the GPL
[IMG META definitions](https://android.googlesource.com/kernel/mediatek/+/4f43e6b499c6d194030df8d2506485db9d5165bd/drivers/staging/imgtec/rogue/rgx_meta.h).

| Status | LDR header field | Offset | Value |
|---|---|---|---|
| Verified bytes | `dev_id` | `0x0`, u32 | `0x01aa5500` |
| Verified bytes / IMG field name | secondary loader (`sl_code`) | `0x4`, u32 | `0x10` |
| Verified bytes / IMG field name | L1 stream (`sl_data`) | `0x8`, u32 | `0x1190` |
| Verified bytes | control, CRC | `0xc`, two u16 | `0`, `0x0caf` |

**Verified:** L1 records use `(u16 command, u16 length, u32 next, operands,
checksum word)`. The `next` chain ends in `0xffffffff`. Referenced L2 blocks
have a 4-byte `(tag,length)` header, payload of `length - 6` bytes, and a
2-byte checksum. The parser found **94 records: 73 LoadMem, 13 ZeroMem,
5 comments, 2 Config, 1 StartThreads**. LoadMem carries destination and L2
offset. ZeroMem carries destination, byte length, and L2 offset. Config and
StartThreads point to L2 records. Every referenced range is within the blob.
Checksums are retained but not validated.

**Verified:** LDR `+0x1128` says `Secondary loader stub 1.0.0.5`. Comments at
`+0x15e8` and `+0x32e4` name Apple's `METAFW_G5P.build/g5p_c0/` thread-1
and thread-0 ELF inputs respectively. No ELF headers are needed by this LDR
container; it carries load records, not an ELF section table.

## Load map and entry

**Verified record destinations**, summarized from `meta-ldr.json`:

| Status | Address region | Contents and evidence |
|---|---|---|
| Verified load range; inferred role | `0x10000000` through `0x10017930`, with holes | Thread-0 program/rodata image; first load L1 `0x2dac`; T0 StartThreads word points to `0x10000000`. Includes literal diagnostic strings. Zero records extend parts of the data area. |
| Verified load range; inferred role | `0x20000000` through `0x200016d0`, with holes | Thread-1 program image; first load L1 `0x1500`; T1 StartThreads word points to `0x20000000`. |
| Verified load range; inferred role | `0x80000080` through `0x8000565c` | Initialized data; L1 `0x324c` onward. ZeroMem creates `0x8000a000..0x8000c3c8`; the two initial stack words fall here. |
| Verified load range; inferred role | `0xe0001000..0xe0002000`, sparse/zero filled | Small local/alias data region; first ZeroMem L1 `0x11e8`. Mapping and physical backing are unresolved. |
| Verified load range; inferred role | `0xc0000000..0xc0000038` | Small startup/vector payload, L1 `0x2d30`; exact role unresolved. |

**Verified:** initialized payload totals 111,264 bytes; ZeroMem records total
26,164 bytes. These META addresses are not ARM physical addresses and must
not be used as Linux DMA addresses. The per-record map is the authoritative
list; the bounding spans above include gaps.

**Verified raw StartThreads record:** L1 at `+0x1cff4`, L2 at `+0x1d120`,
tag `0x8003`, length 38. Its eight payload words are:

```text
1, 0x8000b3c8, 0x20000000, 0,
0, 0x8000a3c8, 0x10000000, 0
```

**Inferred field semantics:** `(thread, SP, PC, catch)` for each four-word
entry. This gives T1 entry `0x20000000`, SP `0x8000b3c8`; T0 entry
`0x10000000`, SP `0x8000a3c8`. The separate ELF comments corroborate two
thread programs. The reset/secondary-loader entry and the ARM host's actual
startup sequence still require reversing; these PC words are not a complete
boot recipe.

**Verified:** two Config records at `+0x1190` and `+0x1cfe4` contain only
type-2 writes (4 and 23 writes). For example the first writes `0xf0f0` to
META addresses `0x04800070`, `0x04801070`, `0x04802070`, `0x04803070`.
The later block includes `0x04830028 = 6`, `0x04830600 = 0xc0c0` and
`0x048000e8 = 0x80`, `0x048010e8 = 0x80`. All writes are preserved in JSON.
**Inferred:** these are META internal configuration, not GPU topology
constants; in particular `0x04830028 = 6` does not prove six USC clusters.

**Verified comparison:** current Linux's loader skips StartThreads and
rebuilds its own boot arguments (`pvr_fw_meta.c:298,465`). Its documented
standard data base is `0x10000000`, bootloader base `0x40000000`, and coremem
code/data bases `0x80000000`/`0x82000000` (`pvr_rogue_meta.h:272,290,315`).
Apple's LDR has program entry words at `0x10000000` and `0x20000000` and
different apparent data usage. **Inferred:** borrow the LDR decoder, not
Linux's segment assignment or boot policy unchanged.
