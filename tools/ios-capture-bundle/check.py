#!/usr/bin/env python3
"""Sanity table for a pulled ios-capture-bundle run, plus the parsed outputs.

  check.py DEVICE_DIR PARSED_DIR [--phases loadmrt,power]

DEVICE_DIR is build/gpu/ios-capture-bundle/<stamp>/device (loadmrt/,
loadmrt-repeat/, power/).  Writes PARSED_DIR/{load,load-linear,mrt2,mrt4}.json
and power-words.{json,md}, prints one line per check, exits 0 only if every
check passes.  Run it while still in iOS: a FAIL means re-run that phase
(new preflight) before leaving iOS.  docs/gpu/experiments/ios-capture-bundle.md
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _load(name: str, path: pathlib.Path):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = mod  # oracles.py's dataclasses look themselves up there
    spec.loader.exec_module(mod)
    return mod


lm = _load("parse_load_mrt", HERE / "parse_load_mrt.py")
pw = _load("parse_power", HERE / "parse_power.py")
oracles = _load("oracles", ROOT / "tools/mesa-a9-loadop-re/oracles.py")

SCENES = ("pass", "pass-linear", "load", "load-linear", "mrt2", "mrt4")
REPEAT = ("pass", "pass-linear")
PARSES = (("load", "load", "pass"), ("load", "load-linear", "pass-linear"),
          ("mrt", "mrt2", "pass"), ("mrt", "mrt4", "pass"))
ORACLE = {"load": "LOAD_PRESERVED", "load-linear": "LOAD_PRESERVED", "mrt2": "MRT_EXACT", "mrt4": "MRT_EXACT"}
MRT_TARGETS = {"mrt2": 2, "mrt4": 4}
PHASES = ("loadmrt", "power")

results: list[tuple[str, bool, str]] = []


def ok(name: str, cond, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


def text(p: pathlib.Path) -> str | None:
    return p.read_text().strip() if p.exists() else None


def scene_files(d: pathlib.Path, scene: str) -> None:
    rc = text(d / f"rc-{scene}.txt")
    post = list(d.glob(f"{scene}-post-*.bin"))
    kern = (d / f"submit-{scene}-000-kernel.bin").exists()
    alloc = (d / f"allocator-{scene}-encoded.txt").exists()
    ok(f"capture {d.name}/{scene}", rc == "0" and post and kern and alloc,
       f"rc={rc} post_regions={len(post)} submit_kernel={kern} allocator={alloc}")


def pitch(d: pathlib.Path) -> int:
    m = re.search(r"bytesPerRow=(\d+)", text(d / "regions-load-linear-buffers.txt") or "")
    return int(m.group(1)) if m else 256


def loadmrt(dev: pathlib.Path, out: pathlib.Path) -> None:
    d, rep = dev / "loadmrt", dev / "loadmrt-repeat"
    for s in SCENES:
        scene_files(d, s)
    for s in REPEAT:
        scene_files(rep, s)
    for scene, want in ORACLE.items():
        try:
            r = oracles._cmd_capture(argparse.Namespace(dir=str(d), mode=scene, pitch=pitch(d)))
            ok(f"oracle {scene}", r.get("class") == want and r.get("linear_backing", want) == want,
               json.dumps(r, sort_keys=True))
        except Exception as exc:  # noqa: BLE001 - every failure is a FAIL line
            ok(f"oracle {scene}", False, f"{type(exc).__name__}: {exc}")
    for kind, scene, ref in PARSES:
        try:
            r = lm.parse(kind, d, scene, ref, rep if rep.is_dir() else None)
        except Exception as exc:  # noqa: BLE001
            ok(f"decode {scene}", False, f"{type(exc).__name__}: {exc}")
            continue
        (out / f"{scene}.json").write_text(json.dumps(r, indent=1, sort_keys=True))
        e, k = r["extract"], r["submit"].get("kernel", {})
        common = (f"diff_error={e.get('diff_error')} semantic_words={e.get('semantic_word_count')} "
                  f"noise_words={e.get('noise_word_count')} kernel_semantic={k.get('semantic_word_count')}")
        if kind == "load":
            bg = e.get("variant_background", {})
            ok(f"decode {scene}", "error" not in bg and not e.get("diff_error"),
               f"bgrnd0={bg.get('bgrnd0')} texunicode={len(bg.get('texunicode_program') or [])}w "
               f"usc={len(bg.get('usc_bytes') or '') // 2}B {common}")
        else:
            v = r["variant_eot"]
            want = MRT_TARGETS[scene]
            ok(f"decode {scene}", "error" not in v and not v.get("pbe_unmapped_descriptors") and not e.get("diff_error")
               and v.get("pbe_target_count") == want and v.get("usc_bytes", "").endswith("0080"),
               f"targets={v.get('pbe_target_count')}/{want} pbe_dwords={v.get('pbe_dword_count')} "
               f"doutu={v.get('doutu_qword')} doutd={len(v.get('doutd') or [])} "
               f"pds_doutd={v.get('pds_doutd_count')} usc={v.get('usc_len')}B {v.get('error', '')} {common}")


def power(dev: pathlib.Path, out: pathlib.Path) -> None:
    d = dev / "power"
    for f in sorted(d.glob("rc-*.txt")):
        ok(f"power {f.stem}", text(f) == "0", f"rc={text(f)}")
    # device-capture.sh: full (7/7), skip-control (6 group-0 records, control
    # skipped), none (no block read).  Older runs have no mode file.
    mode = text(d / "fw-blocks-mode.txt") or "mode=(no fw-blocks-mode.txt)"
    plan = text(d / "1-dry-plan.txt") or ""
    n = len(re.findall(r"^record .* ok$", plan, re.M))
    ctl = re.search(r"^record control .*$", plan, re.M)
    ok("power fw-blocks mode", mode.startswith("mode=full"), mode)
    ok("power dry-plan records", n == 7, f"{n}/7 validated")
    ok("power control record", ctl and ctl.group(0).endswith(" ok"),
       re.sub(r"\s+", " ", ctl.group(0)) if ctl else "no control line in the dry plan")
    r = pw.parse(d)
    (out / "power-words.json").write_text(json.dumps(r, indent=1, sort_keys=True))
    (out / "power-words.md").write_text(pw.markdown(r))
    for tag in pw.TAGS:
        idx = r["index"].get(tag)
        ser = (r["series"] or {}).get(tag)
        ok(f"fw-blocks {tag}", idx and idx["end_bad"] == 0 and ser and "stop=complete" in (ser["footer"] or ""),
           f"end_bad={idx and idx['end_bad']} rows={ser and ser['rows']} {ser and ser['footer']}")
    for c in r["identity"]:
        ok(f"identity {c['word']}", c["ok"], f"want {c['want']}, iOS {c['ios']}")
    io = r.get("ioreport_gpu") or {}
    gps = io.get("GPU Performance States") or {}
    ok("ioreport GPU Performance States", io.get("samples") and gps.get("total_seconds"),
       f"samples={io.get('samples')} seconds={gps.get('total_seconds')} {gps.get('percent')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("device", type=pathlib.Path)
    ap.add_argument("parsed", type=pathlib.Path)
    ap.add_argument("--phases", default=",".join(PHASES),
                    help="comma list of phases this run captured (default: %(default)s)")
    a = ap.parse_args()
    phases = [p for p in a.phases.split(",") if p]
    if not phases or any(p not in PHASES for p in phases):
        ap.error(f"--phases: subset of {','.join(PHASES)}")
    a.parsed.mkdir(parents=True, exist_ok=True)
    for name, fn in (("loadmrt", loadmrt), ("power", power)):
        if name not in phases:
            continue
        if (a.device / name).is_dir():
            try:
                fn(a.device, a.parsed)
            except Exception as exc:  # noqa: BLE001
                ok(f"{name} phase", False, f"{type(exc).__name__}: {exc}")
        else:
            ok(f"{name} phase", False, f"no {a.device / name}")
    for name, good, detail in results:
        print(f"{'ok  ' if good else 'FAIL'} {name:34s} {detail}")
    bad = sum(1 for _, good, _ in results if not good)
    print(f"{len(results) - bad}/{len(results)} checks ok; parsed outputs in {a.parsed}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
