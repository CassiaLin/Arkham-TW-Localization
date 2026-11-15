"""
Core UPK LZO Decompressor & Property Parser for Batman: Arkham Asylum (UE3 ver 576/21).
"""
import struct
import os
from typing import List, Tuple, Dict, Any

def lzo1x_decompress(src: bytes) -> bytes:
    out = bytearray()
    ip = 0
    t = src[ip]; ip += 1
    skip_first_lit = False
    if t > 17:
        t -= 17
        out.extend(src[ip:ip+t]); ip += t
        t = src[ip]; ip += 1
        if t < 16:
            m_pos = len(out) - 1 - 0x800 - (t >> 2) - (src[ip] << 2); ip += 1
            out.append(out[m_pos]); out.append(out[m_pos+1]); out.append(out[m_pos+2])
            t = src[ip - 2] & 3
            if t != 0:
                out.extend(src[ip:ip+t]); ip += t
                t = src[ip]; ip += 1
                skip_first_lit = True
        else:
            skip_first_lit = True
    while True:
        if not skip_first_lit and t < 16:
            if t == 0:
                while src[ip] == 0:
                    t += 255; ip += 1
                t += 15 + src[ip]; ip += 1
            t += 3
            out.extend(src[ip:ip+t]); ip += t
            t = src[ip]; ip += 1
            if t < 16:
                m_pos = len(out) - 1 - 0x800 - (t >> 2) - (src[ip] << 2); ip += 1
                out.append(out[m_pos]); out.append(out[m_pos+1]); out.append(out[m_pos+2])
                t = src[ip - 2] & 3
                if t == 0:
                    t = src[ip]; ip += 1
                    continue
                out.extend(src[ip:ip+t]); ip += t
                t = src[ip]; ip += 1
        skip_first_lit = False
        while True:
            if t >= 64:
                m_pos = len(out) - 1 - ((t >> 2) & 7) - (src[ip] << 3); ip += 1
                m_len = (t >> 5) - 1 + 2
                lit = t & 3
            elif t >= 32:
                m_len = t & 31
                if m_len == 0:
                    while src[ip] == 0:
                        m_len += 255; ip += 1
                    m_len += 31 + src[ip]; ip += 1
                m_len += 2
                v = src[ip] | (src[ip+1] << 8); ip += 2
                m_pos = len(out) - 1 - (v >> 2)
                lit = v & 3
            elif t >= 16:
                m_pos = len(out) - ((t & 8) << 11)
                m_len = t & 7
                if m_len == 0:
                    while src[ip] == 0:
                        m_len += 255; ip += 1
                    m_len += 7 + src[ip]; ip += 1
                v = src[ip] | (src[ip+1] << 8); ip += 2
                m_pos -= (v >> 2)
                if m_pos == len(out):
                    return bytes(out)
                m_pos -= 0x4000
                m_len += 2
                lit = v & 3
            else:
                m_pos = len(out) - 1 - (t >> 2) - (src[ip] << 2); ip += 1
                m_len = 2
                lit = t & 3
            for _ in range(m_len):
                out.append(out[m_pos]); m_pos += 1
            if lit == 0:
                t = src[ip]; ip += 1
                break
            out.extend(src[ip:ip+lit]); ip += lit
            t = src[ip]; ip += 1


class UPKPackage:
    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as f:
            b = f.read()
        self.magic, self.ver, self.lic, self.hdr_size, folder_len = struct.unpack("<IHHII", b[:16])
        self.folder = b[16:16+folder_len]
        off = 16 + folder_len
        (self.pkg_flags, self.name_cnt, self.name_off,
         self.exp_cnt, self.exp_off, self.imp_cnt, self.imp_off, self.dep_off) = struct.unpack("<IIIIIIII", b[off:off+32])
        gen_cnt = struct.unpack("<I", b[off+32+16:off+32+20])[0]
        self.comp_offset = off + 32 + 20 + gen_cnt * 12 + 8
        self.comp_flags, chunk_cnt = struct.unpack("<II", b[self.comp_offset:self.comp_offset+8])
        if self.comp_flags == 2 and chunk_cnt > 0:
            p = self.comp_offset + 8
            chunks = [struct.unpack("<IIII", b[p+i*16:p+i*16+16]) for i in range(chunk_cnt)]
            tail_8 = b[p + chunk_cnt * 16 : p + chunk_cnt * 16 + 8]
            # Build clean 109-byte uncompressed header (clear PKG_StoreCompressed 0x02000000)
            full = bytearray(b[:self.comp_offset])
            clean_flags = self.pkg_flags & ~0x02000000
            struct.pack_into("<I", full, off, clean_flags)
            full.extend(struct.pack("<II", 0, 0))
            full.extend(tail_8)
            assert len(full) == self.name_off, f"Header length mismatch: {len(full)} != {self.name_off}"
            for uo, us, co, cs in chunks:
                tag, blk_sz, c_tot, u_tot = struct.unpack("<IIII", b[co:co+16])
                n_sub = (u_tot + blk_sz - 1) // blk_sz
                sp = co + 16
                subs = [struct.unpack("<II", b[sp+i*8:sp+i*8+8]) for i in range(n_sub)]
                dp = sp + n_sub * 8
                for cs_sub, us_sub in subs:
                    full.extend(b[dp:dp+cs_sub] if cs_sub == us_sub else lzo1x_decompress(b[dp:dp+cs_sub]))
                    dp += cs_sub
            self.data = bytearray(full)
        else:
            self.data = bytearray(b)

        self.names: List[str] = []
        p = self.name_off
        for _ in range(self.name_cnt):
            slen = struct.unpack("<i", self.data[p:p+4])[0]; p += 4
            if slen > 0:
                s = self.data[p:p+slen-1].decode("latin1")
                p += slen + 8
            else:
                s = self.data[p:p+(-slen)*2-2].decode("utf-16le")
                p += (-slen)*2 + 8
            self.names.append(s)

        self.imports: List[Dict[str, Any]] = []
        p = self.imp_off
        for i in range(self.imp_cnt):
            pkg_n, pkg_num, cls_n, cls_num, outer_idx, obj_n, obj_num = struct.unpack("<iiiiiii", self.data[p:p+28])
            p += 28
            self.imports.append({
                "index": i,
                "class_name": self.names[cls_n],
                "name": self.names[obj_n],
                "num": obj_num
            })

        self.exports: List[Dict[str, Any]] = []
        p = self.exp_off
        for i in range(self.exp_cnt):
            cls_idx, sup_idx, pkg_idx, obj_name, obj_num, arch, flags, sz, off_e = struct.unpack("<iiiiiiQii", self.data[p:p+40])
            exp_flags, net_cnt = struct.unpack("<II", self.data[p+40:p+48])
            entry_len = 48 + net_cnt * 4 + 16 + 4
            if cls_idx < 0:
                cls_name = self.imports[-cls_idx - 1]["name"]
            elif cls_idx > 0:
                cls_name = f"Export_{cls_idx}"
            else:
                cls_name = "Class"
            self.exports.append({
                "index": i,
                "class_idx": cls_idx,
                "class_name": cls_name,
                "outer_idx": pkg_idx,
                "name": self.names[obj_name],
                "num": obj_num,
                "full_name": f"{self.names[obj_name]}_{obj_num}" if obj_num > 0 else self.names[obj_name],
                "size": sz,
                "offset": off_e,
                "entry_offset": p,
                "entry_len": entry_len
            })
            p += entry_len

    def parse_properties(self, pos: int) -> Tuple[List[Dict[str, Any]], int]:
        props = []
        buf = self.data
        while True:
            n_idx, n_num = struct.unpack("<ii", buf[pos:pos+8]); pos += 8
            pname = self.names[n_idx]
            if pname == "None":
                return props, pos
            t_idx, t_num, sz, arr_idx = struct.unpack("<iiii", buf[pos:pos+16]); pos += 16
            tname = self.names[t_idx]
            val_pos = pos
            if tname == "BoolProperty":
                val = bool(struct.unpack("<I", buf[pos:pos+4])[0])
                pos += 4
            elif tname == "StrProperty":
                if sz == 0:
                    val = ""
                else:
                    slen = struct.unpack("<i", buf[pos:pos+4])[0]
                    if slen > 0:
                        val = buf[pos+4:pos+4+slen-1].decode("latin1")
                    elif slen < 0:
                        val = buf[pos+4:pos+4+(-slen)*2-2].decode("utf-16le")
                    else:
                        val = ""
                pos += sz
            elif tname == "ArrayProperty":
                cnt = struct.unpack("<I", buf[pos:pos+4])[0]
                epos = pos + 4
                items = []
                for _ in range(cnt):
                    sub_props, epos = self.parse_properties(epos)
                    items.append(sub_props)
                val = items
                pos += sz
            elif tname == "FloatProperty":
                val = struct.unpack("<f", buf[pos:pos+4])[0]
                pos += sz
            elif tname == "IntProperty":
                val = struct.unpack("<i", buf[pos:pos+4])[0]
                pos += sz
            else:
                val = bytes(buf[pos:pos+sz])
                pos += sz
            props.append({
                "name": pname,
                "type": tname,
                "size": sz,
                "arr_idx": arr_idx,
                "val_pos": val_pos,
                "value": val
            })


if __name__ == "__main__":
    p = r"D:\SteamLibrary\steamapps\common\Batman Arkham Asylum GOTY\BmGame\CookedPC\Maps\Admin\Admin_A_LOC_INT.upk"
    pkg = UPKPackage(p)
    for exp in pkg.exports[:6]:
        print(f"Export {exp['index']}: {exp['name']} (size={exp['size']}, off={exp['offset']})")
        if exp["size"] > 20 and exp["index"] >= 4:
            props, end_pos = pkg.parse_properties(exp["offset"] + 4)
            for pr in props:
                if pr["name"] in ("Subtitles", "LocalizedSubtitles", "DialogPriority", "Duration"):
                    print(" ", pr["name"], "->", pr["value"])
