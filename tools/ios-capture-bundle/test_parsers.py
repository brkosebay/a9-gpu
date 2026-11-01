#!/usr/bin/env python3
"""Offline tests for the ios-capture-bundle parsers.

  python3 -I tools/ios-capture-bundle/test_parsers.py

* EOT/PBE decode against the real round-16 A9 capture (pass6, 1 target),
  then a synthetic 2-target EOT data segment written into its pages, and both
  code-heap fallbacks (EOT signature removed; non-profile region count).
* Power parser against fixtures written by the real fw-blocks code
  (tools/ios-gpu-probe/test-fw-blocks.c --fixture) plus synthetic IOReport
  state-reporter plists in ios-ioreport's layout.
* check.py gates on both.
Skips the capture-based tests when build/gpu-userspace/device/out5-20261022T214556Z
is absent (gitignored).
"""

from __future__ import annotations

import argparse
import pathlib
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import check  # noqa: E402
import parse_load_mrt as lm  # noqa: E402
import parse_power as pw  # noqa: E402

PASS6 = ROOT / "build/gpu-userspace/device/out5-20261022T214556Z"
PBE1 = ["0xfb05050a", "0x00007e01", "0x8001c000", "0x80000000", "0x00000000", "0x00000000", "0x00000000"]


def patch(cap, cpu: int, data: bytes) -> None:
    pa = cpu - cpu % cap.space.page
    pg = bytearray(cap.space.pages[pa])
    pg[cpu - pa:cpu - pa + len(data)] = data
    cap.space.pages[pa] = bytes(pg)


@unittest.skipUnless(PASS6.is_dir(), "round-16 capture absent")
class EotDecode(unittest.TestCase):
    def cap(self):
        return lm.Capture(PASS6, "pass6-post", None)

    def test_single_target(self):
        e = lm.eot(self.cap())
        self.assertEqual(e["eot_words"], ["0x0003c040", "0x00002bc0", "0x00000402"])
        self.assertEqual(e["pds_program"], ["0xf0000800", "0xf4000012", "0xd3000000"])
        self.assertEqual((e["pds_doutd_count"], e["pds_doutu_count"]), (1, 1))
        self.assertEqual(e["pbe_dwords"], PBE1)
        self.assertEqual(e["pbe_sets_of_7"], [PBE1])
        self.assertEqual((e["doutu_qword"], e["usc_offset"], e["usc_len"]), ("0x1000002b00", "0x2b00", 66))
        self.assertTrue(e["usc_bytes"].endswith("0080"))

    def test_two_target_data_segment(self):
        cap = self.cap()
        seg = struct.pack("<QII", 0x100064058, 0x10000007, 0) + struct.pack("<QII", 0x100064074, 0x90000007, 0)
        patch(cap, cap.gpu_to_cpu(0x3C040), seg + struct.pack("<Q", 0x1000002B00))
        patch(cap, cap.gpu_to_cpu(0x100064074), struct.pack("<7I", *range(0xA0, 0xA7)))
        e = lm.eot(cap)
        self.assertEqual(len(e["doutd"]), 2)
        self.assertEqual(e["pbe_dword_count"], 14)
        self.assertEqual(e["pbe_sets_of_7"], [PBE1, [f"0x{v:08x}" for v in range(0xA0, 0xA7)]])
        self.assertEqual(e["usc_offset"], "0x2b00")
        self.assertEqual(e["pbe_unmapped_descriptors"], 0)

    def test_code_heap_fallbacks(self):
        cap = self.cap()
        code = cap.code
        for hit in cap.space.find(lm.x.EOT_PDS, 4):  # 0x2bc0 and any other copy
            patch(cap, hit, b"\0" * 12)
        self.assertFalse(cap.space.find(lm.x.EOT_PDS, 4))
        self.assertEqual(cap._find_code(), lm.x.PROFILE["code"])
        saved = lm.x.PROFILE["regions"]
        lm.x.PROFILE["regions"] = 0  # not the round-16 layout: search by launch word
        try:
            self.assertEqual(cap._find_code(), code)
        finally:
            lm.x.PROFILE["regions"] = saved

    def test_record_decoy_off_profile(self):
        """The 2026-11-01 mrt2/mrt4 failure: outside the 31-region profile the
        first plausible background block was a decoy (region 6 +0x21e4, a
        launch-offset table), and record+0x2b8 read garbage.  The finder must
        take the region-base record whose EOT is a DOUTD+DOUTU+halt program."""
        cap = self.cap()
        rec, bg = cap.record, cap.space.read(cap.record + 0x288, 32)
        decoy = cap.bases[6] + 0x21E4 - 0x288 if cap.code != 6 else cap.bases[5] + 0x1000
        patch(cap, decoy + 0x288, bg)                       # plausible background block
        patch(cap, decoy + 0x2B8, struct.pack("<3I", 0x2440, 0x2340, 0x800000))
        saved = lm.x.PROFILE["regions"]
        lm.x.PROFILE["regions"] = 0                          # force the search path
        try:
            self.assertNotEqual(lm.x.Capture._find_record(cap), rec)  # the old finder takes a decoy
            self.assertEqual(cap._find_record(), rec)
            cap.record = cap._find_record()
            self.assertEqual(lm.eot(cap)["pbe_dwords"], PBE1)
            patch(cap, rec + 0x2B8, struct.pack("<3I", 0x2440, 0x2340, 0x800000))
            with self.assertRaises(lm.x.CaptureError):        # no valid record: refuse
                cap._find_record()
        finally:
            lm.x.PROFILE["regions"] = saved

    def test_word_diff_noise(self):
        a = struct.pack("<4I", 1, 2, 3, 4)
        d = lm.word_diff(a, struct.pack("<5I", 1, 9, 3, 7, 5), struct.pack("<4I", 1, 2, 3, 8))
        self.assertEqual(d["noise_word_offsets"], ["0xc"])
        self.assertEqual([s["offset"] for s in d["semantic"]], ["0x4", "0x10"])
        self.assertEqual(d["semantic"][1], {"offset": "0x10", "ref": None, "var": "0x00000005"})


def ioreport_plist(states: dict[str, dict[str, int]]) -> bytes:
    """IOReportChannels with one state reporter per subgroup (ios-record-report layout)."""
    chans = []
    for n, (sub, rows) in enumerate(states.items()):
        provider, cid = 0x100 + n, 0x4750550000000000 + n
        legend = (len(rows) << 32) | 2
        raw = b"".join(struct.pack("<4Q", provider, cid, (i << 48) | (legend & 0xFFFFFFFFFFFF), 0)
                       + struct.pack("<4Q", int.from_bytes(name.encode().ljust(8, b"\0"), "big"), 1, ticks, 0)
                       for i, (name, ticks) in enumerate(rows.items()))
        chans.append({"DriverID": provider, "LegendChannel": [cid, legend, "GPU Stats", sub],
                      "IOReportChannelInfo": {"IOReportChannelUnit": pw.iorep.TICK_UNIT},
                      "IOReportSubGroupName": sub, "RawElements": raw})
    return plistlib.dumps({"IOReportChannels": chans})


class Power(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="icb-test."))
        exe = cls.tmp / "fwbtest"
        subprocess.run(["clang", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                        "-framework", "IOKit", "-framework", "CoreFoundation",
                        str(ROOT / "tools/ios-gpu-probe/test-fw-blocks.c"), "-o", str(exe)], check=True)
        cls.power = cls.tmp / "device/power"
        cls.power.mkdir(parents=True)
        subprocess.run([str(exe), "--fixture", str(cls.power)], check=True, stdout=subprocess.DEVNULL)
        io = cls.power / "ioreport-gpu"
        io.mkdir()
        for i, (p0, p4) in enumerate(((24_000_000, 0), (6_000_000, 18_000_000))):
            (io / f"delta-{i:03}.plist").write_bytes(ioreport_plist({
                "GPU Performance States": {"OFF": p0, "P1": 0, "P4": p4},
                "Fender State": {"ON": 24_000_000}}))
        (io / "log.txt").write_text("READY group=GPU Stats\nsample=1\n")
        for name in ("idle", "load", "after", "metal-stress", "ioreport-gpu"):
            (cls.power / f"rc-{name}.txt").write_text("0\n")
        lines = [f"record {r:8s} fw+0x{s:03x} -> 0xfffffff000010000 ok"
                 for r, s in (("root", 0x220), ("ta", 0x238), ("3d", 0x240), ("cl", 0x248),
                              ("policy", 0x250), ("meta", 0x268), ("control", 0x2f0))]
        (cls.power / "1-dry-plan.txt").write_text("\n".join(lines) + "\n")
        (cls.power / "fw-blocks-mode.txt").write_text("mode=full\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_table(self):
        r = pw.parse(self.power)
        self.assertEqual(r["snapshots"], ["idle-a", "idle-b", "load-a", "load-b", "after-a", "after-b"])
        self.assertTrue(all(c["ok"] for c in r["identity"]))
        rows = {w["word"]: w for w in r["words"]}
        self.assertEqual(rows["meta+0x6d4"]["ios"]["idle-a"], 1)
        self.assertEqual(rows["meta+0x6d4"]["doc_ios"], "1 while powered, 0 off")
        self.assertEqual(rows["meta+0x6d4"]["catalogue_apple_derived"], 0)  # shown, not overruled
        self.assertEqual(rows["control+0x1030"]["ios"]["after-b"], 400)
        self.assertEqual(rows["policy+0x8"]["ios"]["load-a"], 0)
        self.assertEqual(rows["policy+0x8"]["ios"]["load-b"], 2)
        self.assertEqual(r["uncatalogued_nonzero"]["policy"]["0x100"]["idle-a"], "0xdeadbeef")
        self.assertIn("0x8", r["moved_between_snapshots"]["policy"])
        self.assertIn("0x918", r["moved_between_snapshots"]["policy"])
        self.assertEqual(r["accelerator"]["acc+0x5b8"]["idle-a"], "0x00000028")
        self.assertEqual(r["policy_table_3c"]["idle-a"][(0x100 - 0x3C) * 2:(0x104 - 0x3C) * 2], "efbeadde")
        s = r["series"]["load"]
        self.assertEqual(s["columns"]["policy+0x8"]["transitions"], 1)
        self.assertEqual(s["columns"]["policy+0x8"]["distinct"], ["0x00000000", "0x00000002"])
        self.assertIn("stop=complete", s["footer"])
        g = r["ioreport_gpu"]["GPU Performance States"]
        self.assertEqual(g["percent"], {"OFF": 62.5, "P1": 0.0, "P4": 37.5})
        self.assertEqual(g["per_sample_percent"][1], {"OFF": 25.0, "P1": 0.0, "P4": 75.0})
        self.assertEqual(r["ioreport_gpu"]["Fender State"]["percent"], {"ON": 100.0})
        md = pw.markdown(r)
        self.assertIn("Identity: policy+0x4=ok", md)

    def test_check_power(self):
        check.results.clear()
        out = self.tmp / "parsed"
        out.mkdir(exist_ok=True)
        check.power(self.power.parent, out)
        bad = [x for x in check.results if not x[1]]
        self.assertEqual(bad, [])
        self.assertTrue((out / "power-words.md").exists())

    def test_check_identity_fail(self):
        d = self.tmp / "bad/power"
        shutil.copytree(self.power, d)
        for f in d.glob("*-group0.bin"):
            b = bytearray(f.read_bytes())
            b[0x3040 + 4:0x3040 + 8] = struct.pack("<I", 0)
            f.write_bytes(bytes(b))
        check.results.clear()
        (self.tmp / "parsed-bad").mkdir()
        check.power(d.parent, self.tmp / "parsed-bad")
        failed = [x[0] for x in check.results if not x[1]]
        self.assertEqual(failed, ["identity policy+0x4"])

    def degraded(self, name: str, mode: str, control_line: str, keep_blocks: bool) -> list[str]:
        d = self.tmp / f"{name}/power"
        shutil.copytree(self.power, d)
        (d / "fw-blocks-mode.txt").write_text(mode + "\n")
        plan = (d / "1-dry-plan.txt").read_text().splitlines()
        (d / "1-dry-plan.txt").write_text("\n".join(plan[:6] + [control_line]) + "\n")
        if not keep_blocks:
            for f in list(d.glob("idle-*")) + list(d.glob("load-*")) + list(d.glob("after-*")) + list(d.glob("rc-idle*")) \
                    + list(d.glob("rc-load*")) + list(d.glob("rc-after*")) + [d / "1-dry-plan.txt"]:
                f.unlink()
        check.results.clear()
        (self.tmp / f"parsed-{name}").mkdir()
        check.power(d.parent, self.tmp / f"parsed-{name}")
        return [x[0] for x in check.results if not x[1]]

    def test_check_skip_control(self):
        """6 group-0 records dumped, control skipped: only the three control
        gates fail; identity, fw-blocks and IOReport still pass."""
        failed = self.degraded(
            "skip", "mode=skip-control reason=dry plan: record control fw+0x2f0 -> 0xfffffff6ec210000 group "
            "window unresolved", "record control  fw+0x2f0 -> 0xfffffff6ec210000 group window unresolved", True)
        self.assertEqual(failed, ["power fw-blocks mode", "power dry-plan records", "power control record"])

    def test_check_ioreport_without_blocks(self):
        """No fw-blocks at all (tfp0 gone): IOReport is still judged on its own."""
        failed = self.degraded("none", "mode=none reason=tfp0 unavailable", "", False)
        self.assertIn("ioreport GPU Performance States", [x[0] for x in check.results])
        self.assertNotIn("ioreport GPU Performance States", failed)
        self.assertIn("power fw-blocks mode", failed)
        self.assertTrue(all(f.startswith(("power ", "fw-blocks ", "identity ")) for f in failed), failed)


class PbeTargets(unittest.TestCase):
    def test_split(self):
        one = list(range(7))
        self.assertEqual(lm.pbe_targets(one), [one])
        two = one + [0xFFFFFFFF] + list(range(10, 17))
        self.assertEqual(lm.pbe_targets(two), [one, list(range(10, 17))])
        self.assertIsNone(lm.pbe_targets(one + [0] + list(range(10, 17))))  # no separator
        self.assertIsNone(lm.pbe_targets(list(range(14))))
        self.assertIsNone(lm.pbe_targets([]))


PULLED = ROOT / "build/gpu/ios-capture-bundle/20261101T145640Z/device"


@unittest.skipUnless(PULLED.is_dir(), "2026-11-01 device run absent")
class Pulled20261101(unittest.TestCase):
    """Today's pulled bundle: mrt2/mrt4 decode with the fixed record finder;
    load/load-linear keep their 2026-11-01 decode."""

    def mrt(self, scene):
        return lm.parse("mrt", PULLED / "loadmrt", scene, "pass", PULLED / "loadmrt-repeat")

    def test_mrt2(self):
        r = self.mrt("mrt2")
        v = r["variant_eot"]
        self.assertEqual(r["variant_record"], "0xa63dfc000")
        self.assertEqual((v["pbe_dword_count"], v["pbe_target_count"], v["usc_len"]), (15, 2, 66))
        self.assertEqual(v["pbe_targets"][0], PBE1)
        self.assertEqual(v["pbe_targets"][1][2], "0x80020000")
        self.assertEqual((v["doutu_qword"], v["pds_program"]), ("0x1000002b80", ["0xf0000800", "0xf4000012", "0xd3000000"]))
        self.assertIsNone(r["extract"].get("diff_error"))

    def test_mrt4(self):
        v = self.mrt("mrt4")["variant_eot"]
        self.assertEqual((v["pbe_dword_count"], v["pbe_target_count"], v["usc_len"]), (31, 4, 130))
        self.assertEqual([t[2] for t in v["pbe_targets"]], ["0x8001c000", "0x80020000", "0x80024000", "0x80028000"])
        self.assertEqual([t[6] for t in v["pbe_targets"]], ["0x00000000", "0x00000004", "0x00000008", "0x0000000c"])
        self.assertEqual(v["doutu_qword"], "0x1000002cc0")

    def test_load_unchanged(self):
        for scene, ref in (("load", "pass"), ("load-linear", "pass-linear")):
            lp = lm.parse("load", PULLED / "loadmrt", scene, ref, PULLED / "loadmrt-repeat")["load_program"]
            self.assertEqual(lp["bgrnd0"], ["0x28c0", "0x2840"])
            self.assertEqual(len(lp["texunicode_program"]), 4)
            self.assertEqual(len(lp["usc_bytes"]) // 2, 116)


@unittest.skipUnless(PASS6.is_dir(), "round-16 capture absent")
class CheckLoadMrt(unittest.TestCase):
    def test_scene_gates_and_decode(self):
        """pass6 copied under the bundle's scene names: file gates and the MRT
        decode pass; the pixel oracles fail (no rt-*.raw in a round-16 capture)."""
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="icb-lm."))
        try:
            d, rep = tmp / "loadmrt", tmp / "loadmrt-repeat"
            for dst, scenes in ((d, ("pass", "mrt2")), (rep, ("pass",))):
                dst.mkdir()
                for scene in scenes:
                    for f in PASS6.iterdir():
                        for pre in ("pass6-post", "allocator-pass6", "submit-pass6-000"):
                            if f.name.startswith(pre):
                                shutil.copyfile(f, dst / f.name.replace("pass6", scene, 1))
                    (dst / f"rc-{scene}.txt").write_text("0\n")
            check.results.clear()
            for s in ("pass", "mrt2"):
                check.scene_files(d, s)
            check.scene_files(d, "mrt4")
            self.assertEqual([x[1] for x in check.results], [True, True, False])
            r = lm.parse("mrt", d, "mrt2", "pass", rep)
            self.assertEqual(r["mrt_eot"]["pbe_dwords"], PBE1)
            self.assertEqual(r["submit"]["kernel"]["semantic_word_count"], 0)
            self.assertEqual(r["extract"]["semantic_word_count"], 0)
            res = check.oracles
            with self.assertRaises(Exception):
                res._cmd_capture(argparse.Namespace(dir=str(d), mode="mrt2", pitch=256))
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
