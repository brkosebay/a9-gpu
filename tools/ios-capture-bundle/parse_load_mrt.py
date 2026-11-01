#!/usr/bin/env python3
"""LOAD program words and MRT end-of-tile words from the bundle's g5pdump captures.

Offline, evidence only (docs/gpu/experiments/ios-capture-bundle.md).  Built on
tools/mesa-a9-loadop-re/extract_a9_load.py (record/background/DOUTD decode,
relocation-normalized region diff with a repeat-run noise filter) plus:

* a code-heap finder that does not need the single-target EOT PDS signature
  (an N-target EOT program is a different PDS program);
* the EOT object followed to the end: record+0x2b8 -> data segment = DOUTD
  descriptor list (the PBE state dwords it DMAs) + DOUTU qword -> EOT USC,
  and the EOT PDS program until halt;
* the kernel command buffer the kext parses (submit-<scene>-000-kernel.bin,
  G5P_CAPTURE_SUBMIT=1: the ISP/PPP/BGRND/EOT descriptor words) and the
  submit args, diffed word by word against the reference with the repeat
  run's differences removed as noise.

  parse_load_mrt.py load DIR SCENE --ref pass [--repeat RDIR]   SCENE = load | load-linear
  parse_load_mrt.py mrt  DIR SCENE --ref pass [--repeat RDIR]   SCENE = mrt2 | mrt4
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _load(name: str, path: pathlib.Path):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


x = _load("extract_a9_load", ROOT / "tools/mesa-a9-loadop-re/extract_a9_load.py")
PDS_DOUTD_TOP = 0xF0  # DOUTD instruction class (EOT: f0000800)
PDS_DOUTU_TOP = 0xF4  # DOUTU launch (EOT: f4000012, background: f4000002)


class Capture(x.Capture):
    def _find_code(self) -> int:
        """EOT signature first (round-16 layout); else the fixed profile slot;
        else the region whose launch words a background block references."""
        try:
            return super()._find_code()
        except x.CaptureError:
            pass
        if len(self.bases) == x.PROFILE["regions"]:
            return x.PROFILE["code"]
        for i in range(len(self.bases)):
            self.code = i
            try:
                x.Capture._find_record(self)  # only locates the heap; the record is validated below
                return i
            except (x.CaptureError, ValueError, struct.error):
                continue
        raise x.CaptureError("no code heap: no EOT signature and no background launch in any region")

    def _find_record(self) -> int:
        """The user render record outside the round-16 31-region layout.

        MRT scenes add one region per extra attachment (mrt2: 32, mrt4: 34),
        so the fixed slot does not apply, and the base search's first
        plausible background block is a decoy: region 6 holds a table of
        the code heap's launch offsets at +0x21e4.. (2026-11-01 capture).
        Accepted only when the block sits at +0x288 of a region base (the
        record starts its region in every capture) and record+0x2b8 is an
        EOT object whose PDS program DMAs (DOUTD), launches (DOUTU) and
        halts within the bounded read.  Exactly one such record, or refuse."""
        if len(self.bases) == x.PROFILE["regions"]:
            fixed = self.bases[x.PROFILE["record"]]
            if self._bg_plausible(fixed + x.PROFILE["bg"]) and self._eot_plausible(fixed):
                return fixed
        found = [b for b in self.bases
                 if self._bg_plausible(b + x.PROFILE["bg"]) and self._eot_plausible(b)]
        if len(found) != 1:
            raise x.CaptureError(f"no unique render record: {len(found)} region bases carry a "
                                 "background block and a DOUTD+DOUTU+halt EOT program")
        return found[0]

    def _eot_plausible(self, rec: int) -> bool:
        if not self.space.contains(rec + x.PROFILE["eot"], 12):
            return False
        data_off, code_off, _ = x.words(self.space.read(rec + x.PROFILE["eot"], 12))
        if not (0 < data_off < 0x40000 and 0 < code_off < 0x40000 and data_off % 16 == 0 and code_off % 16 == 0):
            return False
        try:
            pds = x.pds_program(self, code_off)
        except ValueError:
            return False
        return (pds[-1] == x.PDS_HALT and any(w >> 24 == PDS_DOUTD_TOP for w in pds)
                and any(w >> 24 == PDS_DOUTU_TOP for w in pds))


def pbe_targets(pbe: list[int]) -> list[list[int]] | None:
    """N render targets as N sets of 7 PBE dwords.  The N-target EOT DOUTD
    moves 8N-1 dwords: each target's 7, separated by one 0xffffffff word
    (mrt2: 15, mrt4: 31; one target: 7).  None when the list is not that
    shape; the raw dwords are reported either way."""
    if not pbe or len(pbe) % 8 != 7 or any(pbe[i] != 0xFFFFFFFF for i in range(7, len(pbe), 8)):
        return None
    return [pbe[i:i + 7] for i in range(0, len(pbe), 8)]


def hexs(ws) -> list[str]:
    return [f"0x{w:08x}" for w in ws]


def eot(cap: Capture) -> dict:
    """The EOT object: record+0x2b8 = {data segment, PDS code, sizeinfo}."""
    data_off, code_off, sizeinfo = x.words(cap.space.read(cap.record + x.PROFILE["eot"], 12, strict=True))
    out = {"eot_words": hexs((data_off, code_off, sizeinfo)), "data_offset": hex(data_off),
           "pds_offset": hex(code_off), "sizeinfo": hex(sizeinfo)}
    pds = x.pds_program(cap, code_off)
    out["pds_program"] = hexs(pds)
    out["pds_doutd_count"] = sum(1 for w in pds if w >> 24 == PDS_DOUTD_TOP)
    out["pds_doutu_count"] = sum(1 for w in pds if w >> 24 == PDS_DOUTU_TOP)
    descs = x.doutd(cap, data_off, limit=16)
    out["doutd"] = descs
    tail = data_off + 16 * len(descs)
    out["data_segment_words"] = hexs(x.words(cap.heap(data_off, 16 * len(descs) + 0x20)))
    (doutu,) = struct.unpack("<Q", cap.heap(tail, 8))
    usc_off = doutu & 0xFFFFFFFF
    out["doutu_qword"] = hex(doutu)
    out["usc_offset"] = hex(usc_off)
    out["usc_temps_field"] = hex(doutu >> 32)
    usc = x.usc_bytes(cap, usc_off)
    out["usc_bytes"] = usc.hex()
    out["usc_len"] = len(usc)
    pbe = [int(w, 16) for d in descs for w in (d["src_words"] or [])]
    out["pbe_dwords"] = hexs(pbe)
    out["pbe_dword_count"] = sum(d["dwords"] for d in descs)
    out["pbe_sets_of_7"] = [hexs(pbe[i:i + 7]) for i in range(0, len(pbe), 7)] if pbe and len(pbe) % 7 == 0 else None
    targets = pbe_targets(pbe)
    out["pbe_targets"] = [hexs(t) for t in targets] if targets else None
    out["pbe_target_count"] = len(targets) if targets else None
    out["pbe_unmapped_descriptors"] = sum(1 for d in descs if d["dwords"] and d["src_words"] is None)
    return out


def word_diff(ref: bytes, var: bytes, rep: bytes | None) -> dict:
    """Word diff var vs ref; indices where ref and its repeat differ are noise."""
    a, b = x.words(ref), x.words(var)
    r = x.words(rep) if rep is not None else None
    n = max(len(a), len(b))
    get = lambda ws, i: ws[i] if i < len(ws) else None  # noqa: E731
    noise = set() if r is None else {i for i in range(max(len(a), len(r))) if get(a, i) != get(r, i)}
    changed = [i for i in range(n) if get(a, i) != get(b, i)]
    semantic = [i for i in changed if i not in noise]
    fmt = lambda v: None if v is None else f"0x{v:08x}"  # noqa: E731
    return {"ref_bytes": len(ref), "var_bytes": len(var), "repeat_bytes": None if rep is None else len(rep),
            "noise_word_offsets": [hex(4 * i) for i in sorted(noise)],
            "changed_word_count": len(changed), "semantic_word_count": len(semantic),
            "semantic": [{"offset": hex(4 * i), "ref": fmt(get(a, i)), "var": fmt(get(b, i))} for i in semantic]}


def submit(d: pathlib.Path, scene: str) -> dict:
    p = lambda s: d / f"submit-{scene}-000{s}"  # noqa: E731
    out = {"text": p(".txt").read_text() if p(".txt").exists() else None}
    for k in ("kernel", "args"):
        out[k] = p(f"-{k}.bin").read_bytes() if p(f"-{k}.bin").exists() else None
    return out


def submit_diff(ref_dir, ref_scene, var_dir, var_scene, rep_dir) -> dict:
    ref, var = submit(ref_dir, ref_scene), submit(var_dir, var_scene)
    rep = submit(rep_dir, ref_scene) if rep_dir else {"kernel": None, "args": None, "text": None}
    out = {"variant_submit_txt": var["text"]}
    for k in ("kernel", "args"):
        if ref[k] is None or var[k] is None:
            out[k] = {"error": f"missing submit-*-000-{k}.bin (G5P_CAPTURE_SUBMIT=1?)"}
            continue
        out[k] = word_diff(ref[k], var[k], rep[k])
        out[k]["variant_words"] = hexs(x.words(var[k]))
    return out


def scene_notes(d: pathlib.Path, scene: str) -> list[str]:
    p = d / f"regions-{scene}-buffers.txt"
    return p.read_text().splitlines() if p.exists() else []


def captures(d, scene, ref_scene, rep_dir):
    var = Capture(d, f"{scene}-post", None)
    ref = Capture(d, f"{ref_scene}-post", None)
    rep = Capture(rep_dir, f"{ref_scene}-post", None) if rep_dir else None
    return ref, var, rep


def parse(kind: str, d: pathlib.Path, scene: str, ref_scene: str, rep_dir: pathlib.Path | None) -> dict:
    ref, var, rep = captures(d, scene, ref_scene, rep_dir)
    out = {"schema": "ios-capture-bundle-loadmrt-v1", "kind": kind, "scene": scene,
           "reference_scene": ref_scene, "repeat_dir": str(rep_dir) if rep_dir else None,
           "qualification": "evidence-only-not-device-ready",
           "variant_code_region": var.code, "variant_record": hex(var.record),
           "scene_notes": scene_notes(d, scene)}
    out["extract"] = x.extract(ref, var, rep)
    for name, cap in (("reference_eot", ref), ("variant_eot", var)):
        try:
            out[name] = eot(cap)
        except (x.CaptureError, ValueError, struct.error) as exc:
            out[name] = {"error": str(exc)}
    out["submit"] = submit_diff(d, ref_scene, d, scene, rep_dir)
    if kind == "load":
        bg = out["extract"].get("variant_background", {})
        out["load_program"] = {k: bg.get(k) for k in (
            "bgrnd0", "bgrnd1", "bgrnd2", "bgrnd3_sizeinfo", "shader_doutu_qword", "shader_launch_word",
            "texunicode_program", "usc_offset", "usc_bytes", "usc_sha256", "bgrnd1_doutd", "bgrnd2_doutd")}
    else:
        v = out["variant_eot"]
        out["mrt_eot"] = {k: v.get(k) for k in (
            "eot_words", "pds_program", "pds_doutd_count", "doutd", "pbe_dwords", "pbe_dword_count",
            "pbe_sets_of_7", "pbe_targets", "pbe_target_count", "doutu_qword", "usc_offset", "usc_bytes", "usc_len")}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("kind", choices=["load", "mrt"])
    ap.add_argument("dir", type=pathlib.Path)
    ap.add_argument("scene")
    ap.add_argument("--ref", default="pass")
    ap.add_argument("--repeat", type=pathlib.Path)
    a = ap.parse_args()
    try:
        result = parse(a.kind, a.dir, a.scene, a.ref, a.repeat)
    except (x.CaptureError, OSError, ValueError, struct.error) as exc:
        print(json.dumps({"class": "REFUSED", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
