#!/usr/bin/env python3
"""Power-words table from the bundle's `ios-gpu-probe fw-blocks` and IOReport captures.

Offline (docs/gpu/experiments/ios-capture-bundle.md).  For every word that
tools/gpu-latency116/l116/catalogue.py or power-words-init-re.md names, it
puts side by side, without choosing between them: the catalogue's
apple_observed / apple_derived / linux_staged, the power-words doc's iOS
expectation and Linux arm A (latency123 boots 2/3), and the live iOS value in
every snapshot (idle/load/after, each -a before and -b after its series).
Also: identity checks, every non-zero word nobody catalogued, words that
moved between snapshots, the 0x160-byte policy+0x3c table, the accelerator
limit fields, the 1 ms series transitions, and IOReport GPU pstate / Fender
residency.

  parse_power.py POWER_DIR [--json OUT.json] [--md OUT.md]
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import pathlib
import plistlib
import re
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


cat = _load("catalogue", ROOT / "tools/gpu-latency116/l116/catalogue.py")
iorep = _load("ios_record_report", ROOT / "scripts/power/ios-record-report.py")

# Record positions (fw-blocks FWB_RECS): (file group, offset in group, size)
RECORDS = {"policy": (0, 0x3040, 0x938), "meta": (0, 0x3A00, 0x78C), "control": (2, 0x0000, 0x2058)}
TAGS = ("idle", "load", "after")

# power-words-init-re.md 5.1-5.3 / 9.2: what iOS's host driver writes (doc strings verbatim-ish)
DOC = {
    ("meta", 0x6C0): "1", ("meta", 0x6C4): "(acc+0x510>>8)&1", ("meta", 0x6C8): "1",
    ("meta", 0x6D4): "1 while powered, 0 off", ("meta", 0x6D8): "u64 0x100000000 (power-bit pair)",
    ("meta", 0x6DC): "domain mask", ("meta", 0x6E4): "200 if g_7b3f402&0x10 else 1",
    ("meta", 0x6FC): "(acc+0x510>>19)&1", ("meta", 0x700): "= +0x6e4", ("meta", 0x710): "4",
    ("meta", 0x714): "5", ("meta", 0x758): "0x14", ("meta", 0x75C): "0x4000", ("meta", 0x760): "0x1020",
    ("control", 0x00): "powered domain mask", ("control", 0x04): "7 if acc+0x510 bit30 else 0",
    ("control", 0x08): "0", ("control", 0x0C): "0", ("control", 0x10): "0 (inhibit mask)",
    ("control", 0x14): "0", ("control", 0x1C): "0", ("control", 0x24): "0x78",
    ("control", 0x30): "(acc+0x1af8&3)==0", ("control", 0x1028): "3348",
    ("control", 0x102C): "40..400 (floor 40), 0 until a client sets it", ("control", 0x1030): "400",
    ("policy", 0x04): "16", ("policy", 0x08): "0 at init, 2 running", ("policy", 0x0C): "4 at init, 2 running",
    ("policy", 0x10): "0x3f800000", ("policy", 0x28): "1", ("policy", 0x2C): "1", ("policy", 0x30): "0",
    ("policy", 0x34): "fw: current pstate index 0..4", ("policy", 0x38): "100", ("policy", 0x630): "0",
    ("policy", 0x638): "f32 (313-1)/313", ("policy", 0x640): "f32 1/313", ("policy", 0x648): "integral gain",
    ("policy", 0x658): "proportional gain", ("policy", 0x668): "95", ("policy", 0x674): "3348",
    ("policy", 0x67C): "5", ("policy", 0x680): "50", ("policy", 0x684): "0", ("policy", 0x688): "4000",
    ("policy", 0x6FC): "400", ("policy", 0x7CC): "400", ("policy", 0x8F8): "mach_absolute_time()",
    ("policy", 0x918): "sampler window (advances)",
}
for _i, _mhz in enumerate((0, 340, 474, 550, 723)):
    DOC[("meta", 0x718 + 4 * _i)] = str(_mhz)
# power-words-init-re.md 6, "Linux arm A (2/3)" column
LINUX_ARM_A = {("meta", 0x6D4): "1", ("meta", 0x710): "1 / 4", ("meta", 0x714): "2 / 5",
               ("control", 0x1028): "3348", ("control", 0x102C): "400", ("control", 0x1030): "400",
               ("policy", 0x04): "16", ("policy", 0x6FC): "400", ("policy", 0x7CC): "400"}
# power-words-init-re.md 9.2 accelerator fields (+ 0x450: source of the policy+0x3c table)
ACC = {0x510: "platform flags", 0x5B8: "gpu-pwr-min-duty-cycle (40)", 0x5C8: "limit input",
       0x5CC: "limit input", 0x5D4: "limit input", 0x5E0: "limit input (1)", 0x638: "filter input",
       0x63C: "filter input", 0xC6C: "sample period (16)", 0x1170: "ctl+0x14 source (0)",
       0x1AF8: "ctl+0x30 source", 0x1C4C: "num perf states (4)"}
IDENTITY = ((("policy", 0x04), 16), (("meta", 0x71C), 340))
KEY_SERIES = ("meta+0x6d4", "meta+0x710", "meta+0x71c", "policy+0x4", "policy+0x8", "policy+0xc",
              "policy+0x34", "control+0x0", "control+0x4", "control+0x1028", "control+0x102c",
              "control+0x1030")


def u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def f32(v: int) -> float:
    return struct.unpack("<f", struct.pack("<I", v))[0]


def read_index(p: pathlib.Path) -> dict:
    out = {"records": {}, "end_bad": None, "series": None, "snaps": []}
    for line in p.read_text().splitlines():
        m = re.match(r'record (\S+) .*status="([^"]*)"', line)
        if m:
            out["records"][m.group(1)] = m.group(2)
        elif line.startswith("end bad="):
            out["end_bad"] = int(line.split("=")[1])
        elif line.startswith("series "):
            out["series"] = dict(kv.split("=", 1) for kv in line.split()[2:])
        elif line.startswith("snap "):
            out["snaps"].append(line)
    return out


def snapshots(d: pathlib.Path) -> dict[str, dict[str, bytes]]:
    """{'idle-a': {'policy': bytes, 'meta': ..., 'control': ..., 'acc': ...}, ...}"""
    snaps = {}
    for tag in TAGS:
        for s in ("a", "b"):
            name = f"{tag}-{s}"
            recs = {}
            for rec, (g, off, size) in RECORDS.items():
                p = d / f"{name}-group{g}.bin"
                if p.exists() and len(p.read_bytes()) >= off + size:
                    recs[rec] = p.read_bytes()[off:off + size]
            acc = d / f"{name}-acc-object.bin"
            if acc.exists():
                recs["acc"] = acc.read_bytes()
            if recs:
                snaps[name] = recs
    return snaps


def word_rows(snaps: dict) -> list[dict]:
    keys = sorted(set(cat.F) | set(DOC) | set(LINUX_ARM_A),
                  key=lambda k: (list(RECORDS).index(k[0]), k[1]))
    rows = []
    for rec, off in keys:
        c = cat.F.get((rec, off), {})
        row = {"word": f"{rec}+0x{off:x}", "name": c.get("name"), "kind": c.get("kind"),
               "catalogue_apple_observed": c.get("apple_observed"), "catalogue_apple_derived": c.get("apple_derived"),
               "catalogue_linux_staged": c.get("linux_staged"), "doc_ios": DOC.get((rec, off)),
               "doc_linux_arm_a": LINUX_ARM_A.get((rec, off)), "ios": {}}
        for name, recs in snaps.items():
            if rec in recs and off + 4 <= len(recs[rec]):
                row["ios"][name] = u32(recs[rec], off)
        rows.append(row)
    return rows


def uncatalogued(snaps: dict) -> dict:
    """Non-zero words in any snapshot that neither catalogue.py nor the doc names."""
    out = {}
    for rec in RECORDS:
        hits = {}
        for name, recs in snaps.items():
            b = recs.get(rec)
            if b is None:
                continue
            for off in range(0, len(b) - 3, 4):
                v = u32(b, off)
                if v and (rec, off) not in cat.F and (rec, off) not in DOC:
                    hits.setdefault(f"0x{off:x}", {})[name] = f"0x{v:08x}"
        out[rec] = hits
    return out


def moved(snaps: dict) -> dict:
    """Words whose value differs between any two snapshots."""
    out = {}
    for rec in RECORDS:
        have = {n: r[rec] for n, r in snaps.items() if rec in r}
        if len(have) < 2:
            continue
        size = min(len(b) for b in have.values())
        out[rec] = {f"0x{off:x}": {n: f"0x{u32(b, off):08x}" for n, b in have.items()}
                    for off in range(0, size - 3, 4) if len({u32(b, off) for b in have.values()}) > 1}
    return out


def series(d: pathlib.Path, tag: str) -> dict | None:
    p = d / f"{tag}-series.csv"
    if not p.exists():
        return None
    lines = p.read_text().splitlines()
    footer = next((ln for ln in lines if ln.startswith("#")), None)
    rows = list(csv.reader(ln for ln in lines if not ln.startswith("#")))
    head, data = rows[0], rows[1:]
    cols = {}
    for i, name in enumerate(head[2:], start=2):
        vals = [int(r[i], 16) for r in data]
        times = [int(r[0]) for r in data]
        trans = [(times[j], vals[j]) for j in range(1, len(vals)) if vals[j] != vals[j - 1]]
        distinct = list(dict.fromkeys(vals))
        if trans or name in KEY_SERIES:
            cols[name] = {"first": f"0x{vals[0]:08x}" if vals else None,
                          "last": f"0x{vals[-1]:08x}" if vals else None,
                          "transitions": len(trans), "distinct": [f"0x{v:08x}" for v in distinct[:12]],
                          "distinct_count": len(distinct),
                          "first_transitions_ms": [round((t - times[0]) / 1e6, 3) for t, _ in trans[:8]]}
    return {"rows": len(data), "footer": footer, "columns": cols}


def ioreport(d: pathlib.Path) -> dict | None:
    if not d.is_dir():
        return None
    out = {"log_tail": (d / "log.txt").read_text().splitlines()[-2:] if (d / "log.txt").exists() else None}
    files = sorted(d.glob("delta-*.plist"))
    out["samples"] = len(files)
    for sub in ("GPU Performance States", "Fender State", "CLTM-induced GPU Performance States"):
        per, totals = [], {}
        for f in files:
            try:
                chans = [c for c in plistlib.loads(f.read_bytes())["IOReportChannels"]
                         if c.get("IOReportSubGroupName") == sub]
                st = {}
                for c in chans:
                    for k, v in iorep.states(c).items():
                        st[k] = st.get(k, 0) + v["ticks"]
                        totals[k] = totals.get(k, 0) + v["ticks"]
            except (KeyError, ValueError, plistlib.InvalidFileException) as exc:
                per.append({"file": f.name, "error": str(exc)})
                continue
            tot = sum(st.values())
            per.append({k: round(100 * v / tot, 1) for k, v in st.items()} if tot else {})
        tot = sum(totals.values())
        out[sub] = {"total_seconds": tot / iorep.TICKS_PER_SECOND,
                    "percent": {k: round(100 * v / tot, 2) for k, v in totals.items()} if tot else {},
                    "per_sample_percent": per}
    return out


def parse(d: pathlib.Path) -> dict:
    snaps = snapshots(d)
    out = {"schema": "ios-capture-bundle-power-v1", "dir": str(d), "snapshots": list(snaps),  # capture order
           "index": {t: read_index(d / f"{t}-index.txt") for t in TAGS if (d / f"{t}-index.txt").exists()}}
    out["identity"] = [{"word": f"{r}+0x{o:x}", "want": want,
                        "ios": {n: u32(s[r], o) for n, s in snaps.items() if r in s}}
                       for (r, o), want in IDENTITY]
    for chk in out["identity"]:
        chk["ok"] = bool(chk["ios"]) and all(v == chk["want"] for v in chk["ios"].values())
    out["words"] = word_rows(snaps)
    for row in out["words"]:
        if row["word"].startswith("policy+0x6") or row["word"].startswith("policy+0x7"):
            row["ios_f32"] = {n: round(f32(v), 6) for n, v in row["ios"].items()}
    out["uncatalogued_nonzero"] = uncatalogued(snaps)
    out["moved_between_snapshots"] = moved(snaps)
    out["policy_table_3c"] = {n: s["policy"][0x3C:0x19C].hex() for n, s in snaps.items() if "policy" in s}
    out["accelerator"] = {f"acc+0x{o:x}": {"meaning": m, **{n: f"0x{u32(s['acc'], o):08x}"
                          for n, s in snaps.items() if "acc" in s}} for o, m in ACC.items()}
    out["accelerator_450_table"] = {n: s["acc"][0x450:0x5B0].hex() for n, s in snaps.items() if "acc" in s}
    out["series"] = {t: series(d, t) for t in TAGS}
    out["ioreport_gpu"] = ioreport(d / "ioreport-gpu")
    return out


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, int):
        return str(v) if v < 0x10000 else f"0x{v:x}"
    return str(v)


def markdown(r: dict) -> str:
    names = r["snapshots"]
    out = ["# iOS power words (ios-capture-bundle)", "",
           "Identity: " + ", ".join(f"{c['word']}={'ok' if c['ok'] else 'FAIL'} (want {c['want']})"
                                    for c in r["identity"]), "",
           "| word | name | kind | cat derived | cat linux | doc iOS | doc Linux arm A | " + " | ".join(names) + " |",
           "|" + "---|" * (7 + len(names))]
    for w in r["words"]:
        out.append(f"| {w['word']} | {w['name'] or ''} | {w['kind'] or ''} | {fmt(w['catalogue_apple_derived'])} | "
                   f"{fmt(w['catalogue_linux_staged'])} | {w['doc_ios'] or ''} | {w['doc_linux_arm_a'] or ''} | "
                   + " | ".join(fmt(w["ios"].get(n)) for n in names) + " |")
    out += ["", "## Series transitions (1 ms, change-compressed)", ""]
    for tag, s in r["series"].items():
        if not s:
            continue
        out.append(f"- **{tag}**: {s['rows']} rows; {s['footer']}")
        for name, c in s["columns"].items():
            out.append(f"  - `{name}`: {c['transitions']} transitions, distinct {', '.join(c['distinct'])}")
    io = r.get("ioreport_gpu") or {}
    if io.get("GPU Performance States"):
        out += ["", "## IOReport GPU Performance States (whole window)", "",
                ", ".join(f"{k} {v}%" for k, v in io["GPU Performance States"]["percent"].items())]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dir", type=pathlib.Path)
    ap.add_argument("--json", type=pathlib.Path)
    ap.add_argument("--md", type=pathlib.Path)
    a = ap.parse_args()
    r = parse(a.dir)
    if a.json:
        a.json.write_text(json.dumps(r, indent=1, sort_keys=True))
    if a.md:
        a.md.write_text(markdown(r))
    if not a.json and not a.md:
        print(markdown(r))
    return 0 if all(c["ok"] for c in r["identity"]) else 3


if __name__ == "__main__":
    sys.exit(main())
