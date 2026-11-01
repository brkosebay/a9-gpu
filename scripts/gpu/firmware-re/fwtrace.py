#!/usr/bin/env python3
"""Offline FWTRACE candidate generator/verifier; never patches an image.

Default validates spec, raw LDR identity, LDR-loaded site bytes, every generated
MINIM encoding and all branches. --binutils also disassembles expanded words.
--deployment-check always fails until the unresolved reservation is reviewed.

branch() emits the 4-byte extended form; branch_short() also emits the proven
2-byte MINIM B/Bcc forms (see minim.encode_branch and the TRM 3.2.7-3.2.9
layout documented there). decode() walks storage bytes through the shared
minim.decode_storage() so there is only one instruction-length rule in the tree.
"""
import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
SPEC = ROOT / 'scripts/gpu/firmware-re/fwtrace-spec.json'
LDR = ROOT / 'build/gpu/extracted/agx-g5p-c0.ldr'
# Filled from the original extracted C0 LDR, never inferred from an input image.
LDR_SHA256 = '33ae20cbbe2498e4f119ba4e9a55467e4e0192d4c86d5bf1903adce63ffa5dca'
BASE = 0x80000000
CODE_STORAGE = 0x80005800
TRACE = 0x43208000
SITES = [
    # name, PC, exact original bytes, marker, interpretation
    ('policy_enter', 0x8000a4b4, '01c18285', 1,
     'Common pre-MTS/retry policy trampoline; not policy implementation entry.'),
    ('policy_return', 0x8000a128, '71c1819c', 2,
     'Return from the first pre-MTS policy call only; retry return not sampled.'),
    ('ready_result', 0x8000a248, '007402c00491', 3,
     'Readiness returned: record raw D0Re0, then replay CMP and conditional branch.'),
    ('service_enter', 0x8000a444, '01c18285', 4,
     'Service trampoline entered with original CALLR link preserved.'),
    ('compute_kick_written', 0x80008f3c, '82b52d4f', 5,
     'Replay original R0+478 store BEFORE marker; marker proves store instruction issued.'),
    ('compute_finish_enter', 0x80008cdc, '05a65d9e', 6,
     'Finish33 cleanup entry; marker precedes original prologue.'),
]


def require(ok, message):
    if not ok:
        raise ValueError(message)


def hx(n):
    return f'0x{n:08x}'


def hwords(*words):
    return struct.pack('<' + 'H' * len(words), *words).hex()


def branch(pc, target, cc=0):
    require((target - pc) % 4 == 0, 'unaligned branch')
    off = (target - pc) // 4
    require(-(1 << 18) <= off < (1 << 18), 'branch out of range')
    u = off & 0x7ffff
    return hwords(0xc000 | (u >> 5), 0x9000 | ((u & 31) << 5) | cc), \
        0xa0000000 | (u << 5) | cc


_MINIM = None


def minim():
    """The one MINIM ISA module; loaded once for every caller in this file."""
    global _MINIM
    if _MINIM is None:
        p = Path(__file__).with_name('minim.py')
        module_spec = importlib.util.spec_from_file_location('fwtrace_minim', p)
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        _MINIM = module
    return _MINIM


def branch_short(pc, target, cc=0, r=0, size=None):
    """Encode one MINIM branch, preferring the proven 2-byte form.

    `cc` is the 4-bit TRM condition code (0=A, 2=NE, 15=NV) and `r` the repeat
    bit; branch() keeps the older 5-bit {CC,R} `cc` field for its 4-byte form.
    size=2 or size=4 pins the footprint; size=None takes the shortest exact
    encoding and raises rather than silently widening or truncating.
    """
    raw, form = minim().encode_branch(pc, target, cc=cc, r=r, size=size)
    words = decode(raw.hex())
    require(len(words) == 1 and words[0][0] == 0 and words[0][1] == raw.hex(),
            f'branch encoding did not round-trip: {raw.hex()} ({form})')
    return raw.hex(), words[0][2]


def decode(raw):
    """Decode storage bytes into (byte offset, raw hex, expanded META word).

    Instruction length, byte order and rejected encodings come from
    minim.decode_storage(), so this file cannot drift from the ISA module.
    """
    out = []
    for record in minim().decode_storage(bytes.fromhex(raw)):
        require(record['expanded'] is not None, f"UNKNOWN instruction {record['raw'].hex()}")
        out.append((record['storage'], record['raw'].hex(), record['expanded']))
    return out


def load_ldr(path):
    """Walk original linked L1 records and L2 payloads, not padded .bin files.

    Identity pin covers original checksum bytes, whose algorithm is unresolved.
    ZeroMem is represented here for ownership checks, even for core zero records
    the Apple packing path skips (core private/stack addresses remain occupied).
    """
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == LDR_SHA256, 'original LDR SHA256 mismatch')
    require(len(data) == 119112, 'LDR size mismatch')
    def u16(o):
        require(0 <= o <= len(data) - 2, 'LDR u16 bounds')
        return struct.unpack_from('<H', data, o)[0]
    def u32(o):
        require(0 <= o <= len(data) - 4, 'LDR u32 bounds')
        return struct.unpack_from('<I', data, o)[0]
    require(u32(0) == 0x01aa5500, 'LDR signature')
    pos = u32(8)
    seen = set()
    memory = {}
    ranges = []
    starts = 0
    while pos != 0xffffffff:
        require(pos not in seen and len(seen) < 128, 'LDR cycle/record limit')
        seen.add(pos)
        cmd, length, next_pos = u16(pos), u16(pos + 2), u32(pos + 4)
        sizes = {0: 20, 4: 24, 5: 16, 3: 16, 0x10: 16}
        require(cmd in sizes and length == sizes[cmd] and pos + length <= len(data), 'L1 record shape')
        l2 = u32(pos + (12 if cmd == 0 else 16 if cmd == 4 else 8))
        tag, n = u16(l2), u16(l2 + 2)
        require(tag == 0x8000 | cmd and n >= 6 and l2 + n <= len(data), 'L2 shape')
        payload = data[l2 + 4:l2 + n - 2]
        if cmd in (0, 4):
            address = u32(pos + 8)
            size = len(payload) if cmd == 0 else u32(pos + 12)
            require(size > 0 and size <= 0x40000 and address + size <= 0x100000000, 'load bounds')
            require(not address & 3 and not size & 3, 'load alignment')
            require(cmd != 4 or not payload, 'ZeroMem has payload')
            ranges.append((address, address + size, cmd))
            for i, byte in enumerate(payload if cmd == 0 else bytes(size)):
                require(address + i not in memory, 'overlapping LDR memory records')
                memory[address + i] = byte
        if cmd == 3:
            starts += 1
            require(len(payload) == 32 and next_pos == 0xffffffff, 'START shape')
        pos = next_pos
    require(len(seen) == 94 and starts == 1, 'LDR record/start count')
    return memory, ranges


def make_spec(memory, ranges):
    sites = []
    for index, (name, pc, original, marker, meaning) in enumerate(SITES):
        storage = BASE + (pc - BASE) // 2
        want = bytes.fromhex(original)
        require(bytes(memory.get(storage + i, 256) for i in range(len(want))) == want,
                f'{name}: LDR-loaded original bytes mismatch')
        stub_storage = CODE_STORAGE + index * 0x80
        stub_pc = BASE + 2 * (stub_storage - BASE)
        address = TRACE + index * 8
        insns = []
        current = stub_pc
        def emit(raw, word, asm):
            nonlocal current
            decoded = decode(raw)
            require(len(decoded) == 1 and decoded[0][2] == word,
                    f'{name}: independent word/decoder mismatch {raw}')
            insns.append({'pc': hx(current), 'load_address': hx(BASE + (current - BASE) // 2),
                          'bytes': raw, 'meta_word': hx(word), 'asm': asm})
            current += len(bytes.fromhex(raw)) * 2
        def original_instructions():
            for _, raw, word in decode(original):
                emit(raw, word, {
                    '01c18285': 'MOVT A0.3,#0x1000',
                    '71c1819c': 'MOV D0.5,TXSTAT',
                    '82b52d4f': 'SETD [D0Ar6+#1144],D0FrT',
                    '05a6': 'MSETL [A0StP++],D0FrT,D0.5',
                    '5d9e': 'MOV D0.5,D1Ar1',
                }[raw])
        # Preserve original R0+478 write ordering and value, then sample marker.
        if name == 'compute_kick_written':
            original_instructions()
        emit('05a6', 0xb92000e8, 'MSETL [A0StP++],D0FrT,D0.5')
        imm = address >> 16
        emit(hwords(0xc001 | ((imm >> 5) << 1), 0x0a02 | ((imm & 31) << 2)),
             0x02200005 | (imm << 3), f'MOVT D0FrT,#{imm:#x}')
        imm = address & 0xffff
        emit(hwords(0xc000 | ((imm >> 5) << 1), 0x3a00 | ((imm & 31) << 2)),
             0x32200000 | (imm << 3), f'OR D0FrT,D0FrT,#{imm:#x}')
        if name == 'ready_result':
            emit('08f205a0', 0xb6010122, 'SETD [D0FrT+#4],D0Re0')
        emit(hwords(0x0a82 | (marker << 2)), 0x02280004 | (marker << 3), f'MOV D0.5,#{marker}')
        emit('08f281a2', 0xb6290022, 'SETD [D0FrT],D0.5')
        emit('9fb76e7f', 0xa727ff6e, 'GETL D0FrT,D1RtP,[A0StP+#-16]')
        emit('bfb7ee7f', 0xa72fffee, 'GETL D0.5,D1.5,[A0StP+#-8]')
        emit('408c', 0x8a000080, 'SUB A0StP,A0StP,#0x10')
        if name == 'ready_result':
            emit('0074', 0x72000000, 'CMP D0Re0,#0')
            raw, word = branch(current, 0x8000a36c, 4)
            emit(raw, word, 'BNE 0x8000a36c (relocated original conditional branch)')
        elif name != 'compute_kick_written':
            original_instructions()
        continuation = pc + len(want) * 2
        raw, word = branch(current, continuation)
        emit(raw, word, f'B {continuation:#x}')
        stub = ''.join(x['bytes'] for x in insns)
        require(len(bytes.fromhex(stub)) <= 0x80, 'stub slot overflow')
        # Short original pair (finish prologue) and extended original both 4 bytes.
        replacement, word = branch(pc, stub_pc)
        if len(want) == 6:
            replacement += 'fe93'  # exact-footprint NV/NOP, unreachable after B
        require(len(bytes.fromhex(replacement)) == len(want), 'replacement footprint')
        require(decode(replacement)[0][2] == word, 'site branch decode')
        sites.append({'name': name, 'meaning': meaning, 'pc': hx(pc),
                      'load_address': hx(storage),
                      'packed_image_offset': hx(0x24000 + storage - 0x80000080),
                      'expected_original': original, 'replacement': replacement,
                      'continuation_pc': hx(continuation), 'stub_pc': hx(stub_pc),
                      'stub_load_address': hx(stub_storage),
                      'stub_packed_image_offset': hx(0x24000 + stub_storage - 0x80000080),
                      'stub_bytes': stub, 'instructions': insns,
                      'trace_address': hx(address), 'trace_marker': marker,
                      'result_address': hx(address + 4) if name == 'ready_result' else None})
    # No LDR load/zero overlaps the proposed extension. This is NOT a proof
    # against dynamic stores or that all physical core RAM is implemented.
    require(all(b <= CODE_STORAGE or a >= CODE_STORAGE + 0x400 for a, b, _ in ranges),
            'LDR overlaps proposed code extension')
    return {'schema': 1, 'status': 'BLOCKED_LOCAL_CODE_RESERVATION_UNPROVEN',
            'deployment_authorized': False,
            'ldr': {'sha256': LDR_SHA256, 'size': 119112,
                    'default_path': str(LDR.relative_to(ROOT))},
            'address_rule': 'load=0x80000000+(execution_pc-0x80000000)/2; data addresses never doubled',
            'code_reservation': {'load_base': hx(CODE_STORAGE), 'size': '0x400',
                'execution_base': '0x8000b000', 'packed_base': '0x29780',
                'old_copy_size': '0x55dc', 'candidate_copy_size': '0x5b80',
                'required_proof': 'No firmware dynamic store, stack, allocator or hardware alias uses 80005800..80005bff; physical RAM and MINIM fetch are valid there.',
                'established': 'No original LDR load or ZeroMem record overlaps. Original payload ends 8000565c; private/stack records start 8000a000. This is only static separation.'},
            'trace_reservation': {'kind': 'NEW_HOST_ALLOCATION_REQUIRED',
                'gpu_va': '0xe1c3208000', 'meta_va': hx(TRACE), 'size': '0x4000',
                'root_old_size': '0x8000', 'root_candidate_size': '0xc000',
                'ownership': 'Extend owned root allocation and mapping by one 16KiB page; exclude page from every object/queue/BO allocator. No existing word is borrowed.',
                'visibility': 'Root META uncached alias; host coherent DMA mapping and dma_rmb before reads. Validate mapping/visibility with separate probe before relying on absent markers.'},
            'preservation': {'saved': ['D0FrT', 'D1RtP', 'D0.5', 'D1.5'],
                'stack_extra_bytes': 16, 'stack_alignment': 8,
                'flags': 'No S/flag-setting stub instruction. ready_result replays original CMP after restoration.',
                'link': 'No CALLR is relocated; original CALLR instructions and D1RtP behavior remain in place.',
                'markers': 'Sticky unique marker per site; no counters, atomics, print loop or ordering assertion across sites. ready_result raw value is separately sampled.'},
            'sites': sites}


def binutils(spec):
    tool = ROOT / 'build/gpu/firmware-re/tools/bin/metag-linux-objdump'
    require(tool.exists(), 'build META binutils first')
    words = []
    assembly = []
    for site in spec['sites']:
        words.extend(int(i['meta_word'], 16) for i in site['instructions'])
        assembly.extend(i['asm'] for i in site['instructions'])
        for _, _, word in decode(site['replacement']):
            words.append(word)
            assembly.append('NOP' if word == 0xa0fffffe else 'B')
    # A compact standard-META word stream avoids all MINIM padding. No target
    # execution and no shared firmware files are modified.
    with tempfile.TemporaryDirectory(prefix='fwtrace-') as directory:
        p = Path(directory) / 'words.bin'
        p.write_bytes(b''.join(struct.pack('<I', w) for w in words))
        cmd = ['docker', 'run', '--rm', '-v', f'{tool.parent.parent}:/tools:ro',
               '-v', f'{directory}:/input:ro', 'linux-a9-kbuild:latest',
               '/tools/bin/metag-linux-objdump', '-D', '-b', 'binary', '-m', 'metag', '/input/words.bin']
        result = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
        require('UNKNOWN' not in result.upper() and '.word' not in result and '.byte' not in result,
                'GNU rejected an expanded instruction')
        rows = [line for line in result.splitlines() if ':' in line and '\t' in line]
        require(len(rows) == len(words), 'GNU disassembly coverage mismatch')
        def normalize(asm):
            asm = asm.split(' (')[0]
            asm = re.sub(r'#(-?0x[0-9a-f]+|-?[0-9]+)',
                         lambda match: '#' + str(int(match[1], 16 if 'x' in match[1] else 10)), asm)
            return re.sub(r'\s+', '', asm).upper()
        for index, (row, word, asm) in enumerate(zip(rows, words, assembly)):
            decoded_asm = row.split('\t')[-1].strip()
            if word >> 24 == 0xa0 and word != 0xa0fffffe:
                cc = word & 31
                off = (word >> 5) & 0x7ffff
                off = (off ^ 0x40000) - 0x40000
                target = (index * 4 + off * 4) & 0xffffffff
                expected_asm = ('BNE' if cc == 4 else 'B') + f' 0x{target:x}'
            else:
                expected_asm = asm
            require(normalize(decoded_asm) == normalize(expected_asm),
                    f'GNU operand mismatch: {decoded_asm!r} != {expected_asm!r}')
        print(result)
        print(f'GNU metag binutils matched all {len(words)} expanded instructions and operands.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ldr', type=Path, default=LDR)
    parser.add_argument('--spec', type=Path, default=SPEC)
    parser.add_argument('--write-spec', action='store_true', help='write deterministic candidate JSON only')
    parser.add_argument('--binutils', action='store_true')
    parser.add_argument('--deployment-check', action='store_true')
    args = parser.parse_args()
    try:
        memory, ranges = load_ldr(args.ldr)
        expected = make_spec(memory, ranges)
        if args.write_spec:
            args.spec.write_text(json.dumps(expected, indent=2) + '\n')
        actual = json.loads(args.spec.read_text())
        require(actual == expected, 'spec differs from verified deterministic candidate')
        if args.binutils:
            binutils(actual)
        print(f"FWTRACE offline audit PASS: {len(SITES)} sites; LDR identity/load bytes, MINIM words, branch destinations and footprints match.")
        print('FWTRACE deployment BLOCKED: local executable reservation/physical mapping not proved; new host trace-page reservation not implemented.')
        if args.deployment_check:
            return 2
        return 0
    except (ValueError, OSError, KeyError, struct.error, subprocess.CalledProcessError) as error:
        print(f'FWTRACE REJECT: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
