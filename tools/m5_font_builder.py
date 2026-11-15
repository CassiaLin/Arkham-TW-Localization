"""
Module 5B (M5-Font-Builder): Pure Python Scaleform GFx DefineFont3 Vector Font Compiler.
Collects unique glyphs from translated Traditional Chinese JSONL files and compiles
a crisp vector DefineFont3 (Tag 75) SWF/GFX font from any TrueType/TTC font (e.g. Microsoft JhengHei).
"""
import os
import sys
import json
import zlib
import struct
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Set, Optional

from fontTools.ttLib import TTFont
from fontTools.pens.basePen import BasePen
from PIL import Image, ImageDraw, ImageFont

ROOT_DIR = Path(__file__).resolve().parent.parent


class BitWriter:
    """Bit-level writer for Adobe SWF SHAPE records."""
    def __init__(self):
        self.buf = bytearray()
        self.cur_byte = 0
        self.bit_pos = 0  # 0..7 (MSB first)

    def write_ub(self, val: int, nbits: int):
        for i in range(nbits - 1, -1, -1):
            bit = (val >> i) & 1
            self.cur_byte = (self.cur_byte << 1) | bit
            self.bit_pos += 1
            if self.bit_pos == 8:
                self.buf.append(self.cur_byte)
                self.cur_byte = 0
                self.bit_pos = 0

    def write_sb(self, val: int, nbits: int):
        if nbits == 0:
            return
        if val < 0:
            val = (1 << nbits) + val
        self.write_ub(val & ((1 << nbits) - 1), nbits)

    def align(self):
        if self.bit_pos > 0:
            self.cur_byte <<= (8 - self.bit_pos)
            self.buf.append(self.cur_byte)
            self.cur_byte = 0
            self.bit_pos = 0

    def get_bytes(self) -> bytes:
        self.align()
        return bytes(self.buf)


def min_sbits(*vals: int) -> int:
    """Minimum number of signed bits needed to represent all given integers."""
    mx = max((abs(v) for v in vals), default=0)
    if mx == 0:
        return 1
    # Check positive/negative range for n bits: [-2^(n-1), 2^(n-1)-1]
    n = 1
    while True:
        lo = -(1 << (n - 1))
        hi = (1 << (n - 1)) - 1
        if all(lo <= v <= hi for v in vals):
            return n
        n += 1


class SWFShapePen(BasePen):
    """Records TrueType glyph contours as SWF DefineFont3 quadratic shape commands."""
    def __init__(self, glyphSet, scale: float):
        super().__init__(glyphSet)
        self.scale = scale
        self.contours: List[List[Tuple]] = []
        self.cur_contour: Optional[List[Tuple]] = None
        self.cur_pt: Tuple[int, int] = (0, 0)
        self.start_pt: Tuple[int, int] = (0, 0)
        self.min_x = 0
        self.max_x = 0
        self.min_y = 0
        self.max_y = 0
        self.has_pts = False

    def _tx(self, pt: Tuple[float, float]) -> Tuple[int, int]:
        x = int(round(pt[0] * self.scale))
        y = int(round(-pt[1] * self.scale))  # Flip Y for SWF
        if not self.has_pts:
            self.min_x = self.max_x = x
            self.min_y = self.max_y = y
            self.has_pts = True
        else:
            if x < self.min_x: self.min_x = x
            if x > self.max_x: self.max_x = x
            if y < self.min_y: self.min_y = y
            if y > self.max_y: self.max_y = y
        return (x, y)

    def _moveTo(self, pt):
        if self.cur_contour:
            self.contours.append(self.cur_contour)
        p = self._tx(pt)
        self.cur_contour = [("move", p)]
        self.cur_pt = p
        self.start_pt = p

    def _lineTo(self, pt):
        p = self._tx(pt)
        if p != self.cur_pt:
            self.cur_contour.append(("line", p))
            self.cur_pt = p

    def _qCurveToOne(self, pt1, pt2):
        c = self._tx(pt1)
        p = self._tx(pt2)
        if c == self.cur_pt and p == self.cur_pt:
            return
        self.cur_contour.append(("qcurve", c, p))
        self.cur_pt = p

    def _curveToOne(self, pt1, pt2, pt3):
        # Fallback if CFF cubic font is passed: approximate with straight line
        p = self._tx(pt3)
        if p != self.cur_pt:
            self.cur_contour.append(("line", p))
            self.cur_pt = p

    def _closePath(self):
        if self.cur_contour is not None:
            if self.cur_pt != self.start_pt:
                self.cur_contour.append(("line", self.start_pt))
                self.cur_pt = self.start_pt
            self.contours.append(self.cur_contour)
            self.cur_contour = None

    def _endPath(self):
        self._closePath()

    def build_swf_shape(self) -> bytes:
        if self.cur_contour:
            self._closePath()
        if not self.contours:
            return b"\x10\x00"  # Empty glyph (space)

        bw = BitWriter()
        # NumFillBits=1, NumLineBits=0
        bw.write_ub(1, 4)
        bw.write_ub(0, 4)

        cur_x, cur_y = 0, 0
        first = True

        for contour in self.contours:
            for cmd in contour:
                if cmd[0] == "move":
                    tx, ty = cmd[1]
                    # StyleChangeRecord: TypeFlag=0
                    bw.write_ub(0, 1)
                    # StateNewStyles=0, StateLineStyle=0, StateFillStyle1=0, StateFillStyle0=(1 if first else 0), StateMoveTo=1
                    bw.write_ub(0, 1)
                    bw.write_ub(0, 1)
                    bw.write_ub(0, 1)
                    bw.write_ub(1 if first else 0, 1)
                    bw.write_ub(1, 1)

                    nb = min_sbits(tx, ty)
                    bw.write_ub(nb, 5)
                    bw.write_sb(tx, nb)
                    bw.write_sb(ty, nb)
                    if first:
                        bw.write_ub(1, 1)  # FillStyle0 = 1
                        first = False
                    cur_x, cur_y = tx, ty

                elif cmd[0] == "line":
                    tx, ty = cmd[1]
                    dx = tx - cur_x
                    dy = ty - cur_y
                    if dx == 0 and dy == 0:
                        continue
                    nb = max(2, min_sbits(dx, dy))
                    bw.write_ub(1, 1)  # TypeFlag=1 (Edge)
                    bw.write_ub(1, 1)  # StraightFlag=1
                    bw.write_ub(nb - 2, 4)
                    if dx != 0 and dy != 0:
                        bw.write_ub(1, 1)  # GeneralLineFlag=1
                        bw.write_sb(dx, nb)
                        bw.write_sb(dy, nb)
                    elif dy == 0:
                        bw.write_ub(0, 1)  # GeneralLineFlag=0
                        bw.write_ub(0, 1)  # VerticalFlag=0 (horizontal)
                        bw.write_sb(dx, nb)
                    else:
                        bw.write_ub(0, 1)  # GeneralLineFlag=0
                        bw.write_ub(1, 1)  # VerticalFlag=1
                        bw.write_sb(dy, nb)
                    cur_x, cur_y = tx, ty

                elif cmd[0] == "qcurve":
                    cx, cy = cmd[1]
                    tx, ty = cmd[2]
                    cdx = cx - cur_x
                    cdy = cy - cur_y
                    adx = tx - cx
                    ady = ty - cy
                    nb = max(2, min_sbits(cdx, cdy, adx, ady))
                    bw.write_ub(1, 1)  # TypeFlag=1 (Edge)
                    bw.write_ub(0, 1)  # StraightFlag=0 (Curved)
                    bw.write_ub(nb - 2, 4)
                    bw.write_sb(cdx, nb)
                    bw.write_sb(cdy, nb)
                    bw.write_sb(adx, nb)
                    bw.write_sb(ady, nb)
                    cur_x, cur_y = tx, ty

        # EndShapeRecord: 6 zero bits
        bw.write_ub(0, 6)
        return bw.get_bytes()


def encode_swf_rect(xmin: int, xmax: int, ymin: int, ymax: int) -> bytes:
    nb = min_sbits(xmin, xmax, ymin, ymax)
    bw = BitWriter()
    bw.write_ub(nb, 5)
    bw.write_sb(xmin, nb)
    bw.write_sb(xmax, nb)
    bw.write_sb(ymin, nb)
    bw.write_sb(ymax, nb)
    return bw.get_bytes()


def collect_charset(games: List[str]) -> List[int]:
    """Collect all unique Unicode codepoints from translated files + ASCII + CJK punctuation."""
    chars: Set[int] = set(range(32, 127))
    # Essential CJK & Taiwan punctuation
    for ch in "　、。，．·；：「」『』（）［］｛｝〈〉《》！？…—～－＋＝％＠＃＆＊／＼｜•●○■□▲△▼▽◆◇★☆※°℃℉":
        chars.add(ord(ch))

    for g in games:
        tdir = ROOT_DIR / "workspace" / g / "translated"
        if not tdir.exists():
            continue
        for jp in tdir.rglob("*.output.jsonl"):
            with open(jp, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        obj = json.loads(s)
                        zh = obj.get("zh", "")
                        for c in zh:
                            cp = ord(c)
                            if cp >= 32:
                                chars.add(cp)
                    except json.JSONDecodeError:
                        continue
        for extra_name in (
            "usm_zh.json",
            "usm_sha256_zh.json",
            "ak_events_zh.json",
            "upk_extra_zh.json",
            "upk_extra_sha256_zh.json",
            "ak_en_zh.json",
            "ak_en_sha256_zh.json",
        ):
            extra_p = ROOT_DIR / "workspace" / g / extra_name
            if extra_p.exists():
                try:
                    for zh in json.loads(extra_p.read_text(encoding="utf-8")).values():
                        for c in zh:
                            cp = ord(c)
                            if cp >= 32:
                                chars.add(cp)
                except (json.JSONDecodeError, OSError):
                    pass
    return sorted(chars)


def build_define_font3(
    ttf_path: str,
    font_number: int,
    font_id: int,
    font_name: str,
    codepoints: List[int],
    lang_code: int = 4,
) -> bytes:
    """Compile an Adobe SWF DefineFont3 (Tag 75) payload for the given codepoints."""
    tt = TTFont(ttf_path, fontNumber=font_number)
    upem = tt["head"].unitsPerEm
    scale = 20480.0 / upem  # DefineFont3 uses 20480 em-square

    cmap = tt.getBestCmap()
    glyph_set = tt.getGlyphSet()
    hmtx = tt["hmtx"]
    hhea = tt["hhea"]

    ascent = int(round(hhea.ascent * scale))
    descent = int(round(abs(hhea.descent) * scale))
    leading = int(round(max(0, hhea.lineGap) * scale))

    # Filter codepoints that exist in TTF (keep space 32 even if empty)
    valid_cps = [cp for cp in codepoints if cp in cmap]
    if 32 not in valid_cps and 32 in cmap:
        valid_cps.insert(0, 32)
    valid_cps = sorted(set(valid_cps))

    shapes: List[bytes] = []
    advances: List[int] = []
    bounds_bytes = bytearray()

    for cp in valid_cps:
        gname = cmap[cp]
        adv_w, _ = hmtx[gname]
        advances.append(int(round(adv_w * scale)))

        pen = SWFShapePen(glyph_set, scale)
        glyph_set[gname].draw(pen)
        shape_data = pen.build_swf_shape()
        shapes.append(shape_data)
        if pen.has_pts:
            bounds_bytes.extend(encode_swf_rect(pen.min_x, pen.max_x, pen.min_y, pen.max_y))
        else:
            bounds_bytes.extend(encode_swf_rect(0, 0, 0, 0))

    num_glyphs = len(valid_cps)
    fname_bytes = font_name.encode("utf-8") + b"\x00"

    out = bytearray()
    # FontID (UI16), Flags=0x8c (HasLayout | WideOffsets | WideCodes), LangCode, FontNameLen
    out.extend(struct.pack("<HBBB", font_id, 0x8C, lang_code, len(fname_bytes)))
    out.extend(fname_bytes)
    out.extend(struct.pack("<H", num_glyphs))

    # OffsetTable (UI32 * num_glyphs) + CodeTableOffset (UI32)
    table_header_size = (num_glyphs + 1) * 4
    cur_off = table_header_size
    offsets = []
    for sh in shapes:
        offsets.append(cur_off)
        cur_off += len(sh)
    code_table_off = cur_off

    for off in offsets:
        out.extend(struct.pack("<I", off))
    out.extend(struct.pack("<I", code_table_off))

    for sh in shapes:
        out.extend(sh)

    for cp in valid_cps:
        out.extend(struct.pack("<H", cp))

    # Layout info
    out.extend(struct.pack("<hhh", ascent, descent, leading))
    for adv in advances:
        out.extend(struct.pack("<h", max(-32768, min(32767, adv))))
    out.extend(bounds_bytes)
    # KerningCount = 0
    out.extend(struct.pack("<H", 0))

    tt.close()
    return bytes(out)


def rebuild_gfx_with_zh_fonts(orig_swf: bytes, ttf_path: str, codepoints: List[int]) -> bytes:
    """Replace all DefineFont3 (Tag 75) tags in a Scaleform GFX/SWF stream with Traditional Chinese fonts."""
    sig = orig_swf[:3]
    ver = orig_swf[3]
    if sig in (b"CFX", b"CWS"):
        body = zlib.decompress(orig_swf[8:])
        out_sig = b"GFX" if sig == b"CFX" else b"FWS"
    else:
        body = orig_swf[8:]
        out_sig = sig

    nbits = body[0] >> 3
    rect_end = ((5 + 4 * nbits + 7) // 8) + 4
    header_tail = body[:rect_end]
    pos = rect_end

    new_tags = bytearray()
    font_cache: Dict[str, bytes] = {}

    while pos < len(body):
        tag_hdr = struct.unpack("<H", body[pos:pos + 2])[0]
        pos += 2
        tag_type = tag_hdr >> 6
        tag_len = tag_hdr & 0x3F
        if tag_len == 0x3F:
            tag_len = struct.unpack("<I", body[pos:pos + 4])[0]
            pos += 4
        tag_payload = body[pos:pos + tag_len]
        pos += tag_len

        if tag_type == 75:
            font_id, _, _, name_len = struct.unpack("<HBBB", tag_payload[:5])
            fname = tag_payload[5:5 + name_len].rstrip(b"\x00").decode("utf-8", errors="replace")
            print(f"  Compiling DefineFont3 for '{fname}' (FontID={font_id}, {len(codepoints)} glyphs)...")
            new_payload = build_define_font3(ttf_path, 0, font_id, fname, codepoints)
        else:
            new_payload = tag_payload

        # Write tag record (always use long tag header if len >= 0x3F)
        if len(new_payload) >= 0x3F:
            new_tags.extend(struct.pack("<HI", (tag_type << 6) | 0x3F, len(new_payload)))
        else:
            new_tags.extend(struct.pack("<H", (tag_type << 6) | len(new_payload)))
        new_tags.extend(new_payload)

        if tag_type == 0:
            break

    full_body = header_tail + bytes(new_tags)
    total_len = 8 + len(full_body)
    return out_sig + struct.pack("<BI", ver, total_len) + full_body


def patch_asylum_startup_upk(startup_upk_path: Path, out_upk_path: Path, ttf_path: str, codepoints: List[int]):
    """Decompress Asylum Startup_INT.upk, replace fonts_en GFxMovieInfo with Traditional Chinese vector fonts, and save."""
    sys.path.insert(0, str(ROOT_DIR / "tools"))
    from upk_core import UPKPackage

    pkg = UPKPackage(str(startup_upk_path))
    target_exp = None
    for e in pkg.exports:
        if e["class_name"] == "GFxMovieInfo" and e["name"] == "fonts_en":
            target_exp = e
            break
    if not target_exp:
        raise RuntimeError("Could not find fonts_en GFxMovieInfo export in Startup_INT.upk")

    old_off = target_exp["offset"]
    old_size = target_exp["size"]
    exp_bytes = pkg.data[old_off:old_off + old_size]

    gfx_idx = exp_bytes.find(b"GFX")
    if gfx_idx == -1:
        gfx_idx = exp_bytes.find(b"CFX")
    if gfx_idx == -1:
        raise RuntimeError("Could not find GFX/CFX signature inside fonts_en export")

    old_swf_len = struct.unpack("<I", exp_bytes[gfx_idx - 4:gfx_idx])[0]
    old_swf = exp_bytes[gfx_idx:gfx_idx + old_swf_len]
    tail_bytes = exp_bytes[gfx_idx + old_swf_len:]  # None property terminator (8 bytes)

    print(f"Rebuilding Asylum fonts_en GFxMovieInfo ({len(codepoints)} glyphs)...")
    new_swf = rebuild_gfx_with_zh_fonts(old_swf, ttf_path, codepoints)

    # Reconstruct export payload:
    # exp_bytes[:gfx_idx - 12] + sz (len(new_swf) + 4) + arr_idx (0) + elem_cnt (len(new_swf)) + new_swf + tail_bytes
    prefix = exp_bytes[:gfx_idx - 12]
    new_exp = bytearray(prefix)
    new_exp.extend(struct.pack("<III", len(new_swf) + 4, 0, len(new_swf)))
    new_exp.extend(new_swf)
    new_exp.extend(tail_bytes)

    # Append new export at end of uncompressed UPK so no other export offsets shift!
    out_data = bytearray(pkg.data)
    new_off = len(out_data)
    new_size = len(new_exp)
    out_data.extend(new_exp)

    # Update ExportTable entry: SerialSize at +32, SerialOffset at +36
    entry_off = target_exp["entry_offset"]
    struct.pack_into("<ii", out_data, entry_off + 32, new_size, new_off)

    # Also rebuild UE3 UFont MediumFont (Export 6 + 1248/1249) and SmallFont (Export 7 + 1252) for subtitles!
    bold_ttf = r"C:\Windows\Fonts\msjhbd.ttc"
    ufont_ttf = bold_ttf if Path(bold_ttf).exists() else ttf_path
    atlas_med = compile_ufont_atlas(ufont_ttf, codepoints, font_px=48, max_pages=2)
    atlas_small = compile_ufont_atlas(ufont_ttf, codepoints, font_px=34, max_pages=1)
    patch_asylum_ufont(out_data, pkg, atlas_med, font_exp_idx=6, tex_exp_indices=[1248, 1249])
    patch_asylum_ufont(out_data, pkg, atlas_small, font_exp_idx=7, tex_exp_indices=[1252])

    out_upk_path.parent.mkdir(parents=True, exist_ok=True)
    out_upk_path.write_bytes(bytes(out_data))
    print(f"Patched Startup_INT.upk written to {out_upk_path} ({len(out_data) / (1024*1024):.2f} MB)")

    # Also patch MediumFont (Export 2 + 17/18) and SmallFont (Export 3 + 19) in CommonGame_LOC_INT.upk
    cg_dst = out_upk_path.parent / "CommonGame_LOC_INT.upk"
    cfg = json.loads((ROOT_DIR / "config" / "game_paths.json").read_text(encoding="utf-8"))
    bkp_cg = Path(cfg["asylum"]["root_dir"]) / "_Backup_TW_Mod" / "BmGame" / "CookedPC" / "CommonGame_LOC_INT.upk"
    cg_src = bkp_cg if bkp_cg.exists() and bkp_cg.stat().st_size > 16 else (Path(cfg["asylum"]["cooked_dir"]) / "CommonGame_LOC_INT.upk")
    if cg_src.exists():
        cg_pkg = UPKPackage(str(cg_src))
        cg_out = bytearray(cg_pkg.data)
        patch_asylum_ufont(cg_out, cg_pkg, atlas_med, font_exp_idx=2, tex_exp_indices=[17, 18])
        patch_asylum_ufont(cg_out, cg_pkg, atlas_small, font_exp_idx=3, tex_exp_indices=[19])
        cg_dst.write_bytes(bytes(cg_out))
        print(f"Patched CommonGame_LOC_INT.upk written to {cg_dst} ({len(cg_out) / (1024*1024):.2f} MB)")

    # Patch ShippingPC-BmGame.exe hardcoded subtitle line height (26px -> 60px for Width >= 800)
    bkp_exe = Path(cfg["asylum"]["root_dir"]) / "_Backup_TW_Mod" / "Binaries" / "ShippingPC-BmGame.exe"
    live_exe = Path(cfg["asylum"]["root_dir"]) / "Binaries" / "ShippingPC-BmGame.exe"
    exe_src = bkp_exe if bkp_exe.exists() and bkp_exe.stat().st_size > 1024 * 1024 else live_exe
    exe_dst = out_upk_path.parent.parent.parent / "Binaries" / "ShippingPC-BmGame.exe"
    if exe_src.exists():
        patch_asylum_exe_line_height(exe_src, exe_dst, line_height=60)


def patch_asylum_exe_line_height(exe_src: Path, exe_dst: Path, line_height: int = 60):
    """Patch hardcoded 26px subtitle line spacing (lea eax, [eax*8+18]) in ShippingPC-BmGame.exe."""
    data = bytearray(exe_src.read_bytes())
    delta = line_height - 18
    if not (0 <= delta <= 127):
        raise ValueError(f"line_height={line_height} out of signed imm8 range")

    old_instr = bytes.fromhex("8d04c512000000")  # lea eax, [eax*8 + 18]
    new_instr = bytes([0x6B, 0xC0, delta, 0x83, 0xC0, 0x12, 0x90])  # imul eax, eax, delta; add eax, 18; nop

    patched = 0
    for off, expected_ctx in [
        (0x009457ED, bytes.fromhex("33c0817c2404200300000f9dc0")),
        (0x00945891, bytes.fromhex("33c081fa200300000f9dc0685c575d0257")),
    ]:
        if data[off:off + 7] == old_instr and expected_ctx in data[off - 32:off]:
            data[off:off + 7] = new_instr
            patched += 1

    if patched != 2:
        raise RuntimeError(f"Expected 2 subtitle line-height sites in {exe_src}, patched {patched}")

    exe_dst.parent.mkdir(parents=True, exist_ok=True)
    exe_dst.write_bytes(bytes(data))
    print(f"Patched ShippingPC-BmGame.exe (line_height={line_height}px at {patched} sites) -> {exe_dst}")


def encode_gray_to_dxt5(raw_pixels: bytes, width: int, height: int) -> bytes:
    """Encode 8-bit grayscale glyph atlas into UE3 PF_DXT5 (white RGB + 3-bit alpha)."""
    # DXT5 alpha palette for a0=255, a1=0: [255, 0, 219, 182, 146, 109, 73, 36]
    palette = (255, 0, 219, 182, 146, 109, 73, 36)
    lut = [min(range(8), key=lambda idx: abs(v - palette[idx])) for v in range(256)]
    empty_block = b"\x00" * 8 + b"\xff\xff\xff\xff\x00\x00\x00\x00"
    hdr_alpha = b"\xff\x00"
    rgb_white = b"\xff\xff\xff\xff\x00\x00\x00\x00"

    out = bytearray((width // 4) * (height // 4) * 16)
    out_pos = 0
    for by in range(0, height, 4):
        r0 = by * width
        r1 = r0 + width
        r2 = r1 + width
        r3 = r2 + width
        for bx in range(0, width, 4):
            p0 = raw_pixels[r0 + bx:r0 + bx + 4]
            p1 = raw_pixels[r1 + bx:r1 + bx + 4]
            p2 = raw_pixels[r2 + bx:r2 + bx + 4]
            p3 = raw_pixels[r3 + bx:r3 + bx + 4]
            if not (any(p0) or any(p1) or any(p2) or any(p3)):
                out[out_pos:out_pos + 16] = empty_block
                out_pos += 16
                continue
            bits = 0
            shift = 0
            for row in (p0, p1, p2, p3):
                for v in row:
                    bits |= lut[v] << shift
                    shift += 3
            out[out_pos:out_pos + 2] = hdr_alpha
            out[out_pos + 2:out_pos + 8] = bits.to_bytes(6, "little")
            out[out_pos + 8:out_pos + 16] = rgb_white
            out_pos += 16
    return bytes(out)


def compile_ufont_atlas(
    ttf_path: str,
    codepoints: List[int],
    font_px: int = 48,
    max_pages: int = 2,
) -> Tuple[List[bytes], List[Tuple[int, int, int, int, int, int]], List[Tuple[int, int]], int]:
    """Rasterize glyphs onto one or more 2048x2048 PF_DXT5 atlas pages and build UE3 UFont Characters + CharRemap."""
    tex_size = 2048
    img = Image.new("L", (tex_size, tex_size), 0)
    draw = ImageDraw.Draw(img)
    pil_font = ImageFont.truetype(ttf_path, font_px)

    pages_dxt5: List[bytes] = []
    char_records: List[Tuple[int, int, int, int, int, int]] = []
    remap_pairs: List[Tuple[int, int]] = []

    cx, cy = 2, 2
    row_h = font_px + 6
    pad = 2
    page_idx = 0
    cp_set = set(codepoints)

    # Trim unused top ascender padding so tall glyphs/descenders fit cleanly within row_h
    min_y1 = 999
    for cp in sorted(set(range(32, 127)) | cp_set):
        bb = pil_font.getbbox(chr(cp))
        if bb:
            min_y1 = min(min_y1, bb[1])
    top_trim = max(0, min_y1) if min_y1 < 999 else 0

    # First 256 entries (0..255) map 1-to-1 (cp -> cp) just like original UE3 UFont
    for cp in range(256):
        if 32 <= cp <= 126 or cp in cp_set:
            ch = chr(cp)
            bbox = pil_font.getbbox(ch)
            if bbox:
                adv = int(round(pil_font.getlength(ch)))
                w = max(adv, bbox[2] - bbox[0] + 1)
                h = row_h
            else:
                w = font_px // 2
                h = row_h
            if cx + w + pad >= tex_size:
                cx = 2
                cy += row_h + pad
            if cp != 32:
                draw.text((cx, cy - top_trim), ch, fill=255, font=pil_font)
            char_records.append((cx, cy, w, h, page_idx, 0))
            remap_pairs.append((cp, cp))
            cx += w + pad
        else:
            char_records.append((0, 0, 0, 0, 0, 0))
            remap_pairs.append((cp, cp))

    for cp in sorted(cp_set):
        if cp < 256:
            continue
        ch = chr(cp)
        bbox = pil_font.getbbox(ch)
        if bbox:
            adv = int(round(pil_font.getlength(ch)))
            w = max(adv, bbox[2] - bbox[0] + 1)
            h = row_h
        else:
            w = font_px // 2
            h = row_h

        if cx + w + pad >= tex_size:
            cx = 2
            cy += row_h + pad
        if cy + h + pad >= tex_size:
            pages_dxt5.append(encode_gray_to_dxt5(img.tobytes(), tex_size, tex_size))
            page_idx += 1
            if page_idx >= max_pages:
                break
            img = Image.new("L", (tex_size, tex_size), 0)
            draw = ImageDraw.Draw(img)
            cx, cy = 2, 2

        draw.text((cx, cy - top_trim), ch, fill=255, font=pil_font)
        idx = len(char_records)
        char_records.append((cx, cy, w, h, page_idx, 0))
        remap_pairs.append((cp, idx))
        cx += w + pad

    if len(pages_dxt5) < max_pages:
        pages_dxt5.append(encode_gray_to_dxt5(img.tobytes(), tex_size, tex_size))
    while len(pages_dxt5) < max_pages:
        empty_img = Image.new("L", (tex_size, tex_size), 0)
        pages_dxt5.append(encode_gray_to_dxt5(empty_img.tobytes(), tex_size, tex_size))

    print(f"  Compiled UE3 UFont atlas ({font_px}px, {len(char_records)} entries onto {len(pages_dxt5)}x {tex_size}x{tex_size} PF_DXT5 pages)...")
    return pages_dxt5, char_records, remap_pairs, tex_size


def patch_asylum_ufont(
    out_data: bytearray,
    pkg,
    atlas: Tuple[List[bytes], List[Tuple[int, int, int, int, int, int]], List[Tuple[int, int]], int],
    font_exp_idx: int = 6,
    tex_exp_indices: List[int] = [1248, 1249],
):
    """Rebuild UE3 UFont + PF_DXT5 Texture2D pages (2048x2048) for Asylum in-game/cutscene subtitles."""
    pages_dxt5, char_records, remap_pairs, tex_size = atlas

    # 1. Patch Texture2D pages (PageA, PageB, ...)
    for tex_exp_idx, dxt5_pixels in zip(tex_exp_indices, pages_dxt5):
        tex_exp = pkg.exports[tex_exp_idx]
        t_start = tex_exp["offset"]
        t_end = t_start + tex_exp["size"]
        props, end_pos = pkg.parse_properties(t_start + 4)

        tex_header = bytearray(pkg.data[t_start:end_pos])
        for p in props:
            rel_val = p["val_pos"] - t_start
            if p["name"] in ("SizeX", "SizeY"):
                struct.pack_into("<i", tex_header, rel_val, tex_size)
            elif p["name"] == "MipTailBaseIdx":
                struct.pack_into("<i", tex_header, rel_val, 11)  # log2(2048)

        guid_bytes = pkg.data[t_end - 16:t_end]
        new_tex_start = len(out_data)
        new_tex_buf = bytearray(tex_header)
        # SourceArt FByteBulkData (16 bytes): flags=0, cnt=0, sz_disk=0, off=new_tex_start + len + 16
        src_art_off = new_tex_start + len(new_tex_buf) + 16
        new_tex_buf.extend(struct.pack("<IIII", 0, 0, 0, src_art_off))
        # Mips.Num() = 1
        new_tex_buf.extend(struct.pack("<I", 1))
        # Mips[0] FByteBulkData (16 bytes)
        mip_data_off = new_tex_start + len(new_tex_buf) + 16
        new_tex_buf.extend(struct.pack("<IIII", 0, len(dxt5_pixels), len(dxt5_pixels), mip_data_off))
        new_tex_buf.extend(dxt5_pixels)
        # Mip SizeX, SizeY + 16-byte GUID
        new_tex_buf.extend(struct.pack("<II", tex_size, tex_size))
        new_tex_buf.extend(guid_bytes)

        out_data.extend(new_tex_buf)
        struct.pack_into("<ii", out_data, tex_exp["entry_offset"] + 32, len(new_tex_buf), new_tex_start)

    # 2. Patch UFont (MediumFont / SmallFont)
    f_exp = pkg.exports[font_exp_idx]
    f_start = f_exp["offset"]
    f_end = f_start + f_exp["size"]
    f_buf = pkg.data[f_start:f_end]

    # Parse top-level properties of UFont
    pos = 4
    chars_val_rel = None
    chars_old_sz = 0
    none_end_rel = None
    while pos < len(f_buf):
        n_idx, _ = struct.unpack_from("<ii", f_buf, pos)
        pname = pkg.names[n_idx]
        pos += 8
        if pname == "None":
            none_end_rel = pos
            break
        t_idx, _, sz, _ = struct.unpack_from("<iiii", f_buf, pos)
        tname = pkg.names[t_idx]
        pos += 16
        if pname == "Characters":
            chars_val_rel = pos
            chars_old_sz = sz
        if tname == "StructProperty":
            pos += 8 + sz
        elif tname == "BoolProperty":
            pos += 4
        else:
            pos += sz

    if chars_val_rel is None or none_end_rel is None:
        raise RuntimeError(f"Could not parse UFont Characters/None offsets for {f_exp['name']}")

    new_chars_blob = bytearray(struct.pack("<I", len(char_records)))
    for rec in char_records:
        new_chars_blob.extend(struct.pack("<IIIIbi", *rec))

    new_remap_blob = bytearray(struct.pack("<I", len(remap_pairs)))
    for cp, idx in remap_pairs:
        new_remap_blob.extend(struct.pack("<HH", cp, idx))

    new_font_buf = bytearray(f_buf[:chars_val_rel - 8])
    new_font_buf.extend(struct.pack("<II", len(new_chars_blob), 0))  # new sz, arr_idx=0
    new_font_buf.extend(new_chars_blob)
    new_font_buf.extend(f_buf[chars_val_rel + chars_old_sz:none_end_rel])
    new_font_buf.extend(new_remap_blob)

    new_font_start = len(out_data)
    out_data.extend(new_font_buf)
    struct.pack_into("<ii", out_data, f_exp["entry_offset"] + 32, len(new_font_buf), new_font_start)


def decomp_upk_modern_clean(b: bytes) -> Tuple[bytes, int, int, int]:
    """Decompress UE3 v805/v807/v863 UPK into a clean uncompressed UPK with PKG_StoreCompressed cleared."""
    sys.path.insert(0, str(ROOT_DIR / "tools"))
    from upk_core import lzo1x_decompress

    magic, ver, lic, hdr_size, folder_len = struct.unpack("<IHHII", b[:16])
    off = 16 + folder_len
    pkg_flags, name_cnt, name_off, exp_cnt, exp_off, imp_cnt, imp_off, dep_off = struct.unpack("<IIIIIIII", b[off:off+32])
    gen_cnt = struct.unpack("<I", b[off+64:off+68])[0]
    c_off = off + 68 + gen_cnt * 12 + 8
    comp_flags, chunk_cnt = struct.unpack("<II", b[c_off:c_off+8])
    if comp_flags == 0 or chunk_cnt == 0:
        return b, exp_cnt, exp_off, hdr_size

    tail_len = name_off - (c_off + 8)
    tail_start = c_off + 8 + chunk_cnt * 16
    full = bytearray(b[:c_off])
    struct.pack_into("<I", full, off, pkg_flags & ~0x02000000)
    full.extend(struct.pack("<II", 0, 0))
    full.extend(b[tail_start:tail_start + tail_len])
    assert len(full) == name_off, f"Clean header mismatch: {len(full)} != {name_off}"

    p = c_off + 8
    for _ in range(chunk_cnt):
        uo, us, co, cs = struct.unpack("<IIII", b[p:p+16])
        p += 16
        tag, blk_sz, c_tot, u_tot = struct.unpack("<IIII", b[co:co+16])
        n_sub = (u_tot + blk_sz - 1) // blk_sz
        sp = co + 16
        subs = [struct.unpack("<II", b[sp+i*8:sp+i*8+8]) for i in range(n_sub)]
        dp = sp + n_sub * 8
        for scs, sus in subs:
            sb = b[dp:dp+scs]
            dp += scs
            if scs == sus:
                full.extend(sb)
            elif comp_flags == 1:
                full.extend(zlib.decompress(sb))
            else:
                full.extend(lzo1x_decompress(sb))
    return bytes(full), exp_cnt, exp_off, hdr_size


def patch_modern_fonts_upk(game: str, src_upk_path: Path, out_upk_path: Path, ttf_path: str, codepoints: List[int]):
    """Patch fonts_en.upk for City, Origins, or Knight with Traditional Chinese DefineFont3 vector glyphs."""
    raw = src_upk_path.read_bytes()
    full, exp_cnt, exp_off, hdr_size = decomp_upk_modern_clean(raw)

    # Locate the GFxMovieInfo export entry (last export spanning to EOF)
    entry_pos = None
    serial_size = 0
    serial_off = 0
    for epos in range(exp_off, hdr_size - 8):
        sz, off_e = struct.unpack("<ii", full[epos:epos+8])
        if 1000 < sz <= len(full) and hdr_size <= off_e <= hdr_size + 64 and off_e + sz == len(full):
            entry_pos = epos
            serial_size = sz
            serial_off = off_e
            break
    if entry_pos is None:
        raise RuntimeError(f"[{game}] Could not locate fonts_en ExportTable entry in {src_upk_path}")

    exp_bytes = full[serial_off:serial_off + serial_size]
    gfx_idx = exp_bytes.find(b"CFX")
    if gfx_idx == -1:
        gfx_idx = exp_bytes.find(b"GFX")
    if gfx_idx == -1:
        raise RuntimeError(f"[{game}] Could not find CFX/GFX inside fonts_en export")

    old_swf_len = struct.unpack("<I", exp_bytes[gfx_idx - 4:gfx_idx])[0]
    old_swf = exp_bytes[gfx_idx:gfx_idx + old_swf_len]
    tail_bytes = exp_bytes[gfx_idx + old_swf_len:]

    print(f"[{game}] Rebuilding fonts_en.upk ({len(codepoints)} glyphs)...")
    new_swf = rebuild_gfx_with_zh_fonts(old_swf, ttf_path, codepoints)

    new_exp = bytearray(exp_bytes[:gfx_idx - 12])
    new_exp.extend(struct.pack("<III", len(new_swf) + 4, 0, len(new_swf)))
    new_exp.extend(new_swf)
    new_exp.extend(tail_bytes)

    out_data = bytearray(full[:serial_off])
    out_data.extend(new_exp)
    struct.pack_into("<i", out_data, entry_pos, len(new_exp))

    out_upk_path.parent.mkdir(parents=True, exist_ok=True)
    out_upk_path.write_bytes(bytes(out_data))
    print(f"[{game}] Patched fonts_en.upk written to {out_upk_path} ({len(out_data) / (1024*1024):.2f} MB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Scaleform GFx Traditional Chinese DefineFont3 font")
    parser.add_argument("--games", nargs="+", default=["asylum", "city", "origins", "knight"])
    parser.add_argument("--ttf", default=r"C:\Windows\Fonts\msjh.ttc")
    parser.add_argument("--test-only", action="store_true")
    parser.add_argument("--patch-asylum", action="store_true")
    parser.add_argument("--patch-all", action="store_true")
    args = parser.parse_args()

    cps = collect_charset(args.games)
    print(f"Collected {len(cps)} unique Unicode codepoints across {args.games}")
    if args.test_only:
        payload = build_define_font3(args.ttf, 0, 1, "Rockwell WGL", cps[:200])
        print(f"Test DefineFont3 payload built: {len(payload)} bytes")
    elif args.patch_asylum or args.patch_all:
        cfg = json.loads((ROOT_DIR / "config" / "game_paths.json").read_text(encoding="utf-8"))
        if args.patch_asylum or args.patch_all:
            src_upk = Path(cfg["asylum"]["startup_upk"])
            dst_upk = ROOT_DIR / "dist" / "Arkham_Asylum_TW_Mod" / "BmGame" / "CookedPC" / "Startup_INT.upk"
            patch_asylum_startup_upk(src_upk, dst_upk, args.ttf, collect_charset(["asylum"]))
        if args.patch_all:
            for g, rel in [
                ("city", "BmGame/CookedPCConsole/fonts_en.upk"),
                ("origins", "SinglePlayer/BMGame/CookedPCConsole/fonts_en.upk"),
                ("knight", "BMGame/CookedPCConsole/fonts_en.upk"),
            ]:
                src = Path(cfg[g]["root_dir"]) / rel
                dst = ROOT_DIR / "dist" / f"Arkham_{g.capitalize()}_TW_Mod" / rel
                patch_modern_fonts_upk(g, src, dst, args.ttf, collect_charset([g]))
