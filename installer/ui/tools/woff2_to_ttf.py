# -*- coding: utf-8 -*-
"""WOFF2 -> TTF без внешних библиотек. Brotli берём из встроенного zlib Node.js.
WPF не читает woff2, поэтому один раз превращаем шрифты Egoist DS (OFL) в обычные TTF.
Использование: woff2_to_ttf.py in.woff2 out.ttf
"""
import struct
import subprocess
import sys

TAGS = ["cmap", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post", "cvt ", "fpgm", "glyf", "loca", "prep", "CFF ",
        "VORG", "EBDT", "EBLC", "gasp", "hdmx", "kern", "LTSH", "PCLT", "VDMX", "vhea", "vmtx", "BASE", "GDEF", "GPOS",
        "GSUB", "EBSC", "JSTF", "MATH", "CBDT", "CBLC", "COLR", "CPAL", "SVG ", "sbix", "acnt", "avar", "bdat", "bloc",
        "bsln", "cvar", "fdsc", "feat", "fmtx", "fvar", "gvar", "hsty", "just", "lcar", "mort", "morx", "opbd", "prop",
        "trak", "Zapf", "Silf", "Glat", "Gloc", "Feat", "Sill"]


def brotli(data):
    js = ("const z=require('zlib');const c=[];process.stdin.on('data',d=>c.push(d))"
          ".on('end',()=>process.stdout.write(z.brotliDecompressSync(Buffer.concat(c))))")
    return subprocess.run(["node", "-e", js], input=data, capture_output=True, check=True).stdout


class R:
    def __init__(s, b, p=0):
        s.b, s.p = b, p

    def u8(s):
        v = s.b[s.p]
        s.p += 1
        return v

    def u16(s):
        v = struct.unpack_from(">H", s.b, s.p)[0]
        s.p += 2
        return v

    def i16(s):
        v = struct.unpack_from(">h", s.b, s.p)[0]
        s.p += 2
        return v

    def u32(s):
        v = struct.unpack_from(">I", s.b, s.p)[0]
        s.p += 4
        return v

    def base128(s):
        v = 0
        for _ in range(5):
            c = s.u8()
            v = (v << 7) | (c & 0x7F)
            if not c & 0x80:
                return v
        raise ValueError("base128")

    def u255(s):
        c = s.u8()
        if c == 253:
            return s.u16()
        if c == 255:
            return 253 + s.u8()
        if c == 254:
            return 506 + s.u8()
        return c


def sgn(flag, v):
    return v if flag & 1 else -v


def rebuild_glyf(d):
    r = R(d)
    r.u16()
    r.u16()
    n = r.u16()
    r.u16()
    sizes = [r.u32() for _ in range(7)]
    off = r.p
    st = []
    for s in sizes:
        st.append(R(d, off))
        off += s
    ncs, nps, fls, gls, cps, bbs, ins = st
    bblen = 4 * ((n + 31) >> 5)
    bbmap = d[bbs.p:bbs.p + bblen]
    bbs.p += bblen
    out = []
    xmins = []
    for g in range(n):
        nc = ncs.i16()
        has_bb = bbmap[g >> 3] & (0x80 >> (g & 7))
        if nc == 0:
            out.append(b"")
            xmins.append(0)
            continue
        if nc > 0:
            counts = [nps.u255() for _ in range(nc)]
            total = sum(counts)
            flags = [fls.u8() for _ in range(total)]
            xs = []
            ys = []
            on_flags = []
            x = y = 0
            for f in flags:
                on = not (f >> 7)
                f &= 0x7F
                if f < 10:
                    dx = 0
                    dy = sgn(f, ((f & 14) << 7) + gls.u8())
                elif f < 20:
                    dx = sgn(f, (((f - 10) & 14) << 7) + gls.u8())
                    dy = 0
                elif f < 84:
                    b = f - 20
                    b0 = gls.u8()
                    dx = sgn(f, 1 + (b & 0x30) + (b0 >> 4))
                    dy = sgn(f >> 1, 1 + ((b & 0x0C) << 2) + (b0 & 0xF))
                elif f < 120:
                    b = f - 84
                    b0 = gls.u8()
                    b1 = gls.u8()
                    dx = sgn(f, 1 + ((b // 12) << 8) + b0)
                    dy = sgn(f >> 1, 1 + (((b % 12) >> 2) << 8) + b1)
                elif f < 124:
                    b0 = gls.u8()
                    b1 = gls.u8()
                    b2 = gls.u8()
                    dx = sgn(f, (b0 << 4) + (b1 >> 4))
                    dy = sgn(f >> 1, ((b1 & 0xF) << 8) + b2)
                else:
                    b0 = gls.u8()
                    b1 = gls.u8()
                    b2 = gls.u8()
                    b3 = gls.u8()
                    dx = sgn(f, (b0 << 8) + b1)
                    dy = sgn(f >> 1, (b2 << 8) + b3)
                x += dx
                y += dy
                xs.append(x)
                ys.append(y)
                on_flags.append(1 if on else 0)
            il = gls.u255()
            instr = d[ins.p:ins.p + il]
            ins.p += il
            if has_bb:
                bbox = struct.unpack_from(">4h", d, bbs.p)
                bbs.p += 8
            else:
                bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 0, 0)
            g_ = struct.pack(">h4h", nc, *bbox)
            e = -1
            for c in counts:
                e += c
                g_ += struct.pack(">H", e)
            g_ += struct.pack(">H", il) + instr + bytes(on_flags)
            px = 0
            for v in xs:
                g_ += struct.pack(">h", v - px)
                px = v
            py = 0
            for v in ys:
                g_ += struct.pack(">h", v - py)
                py = v
            xmins.append(bbox[0])
        else:
            start = cps.p
            more = True
            hasins = False
            while more:
                f = cps.u16()
                cps.u16()
                cps.p += 4 if f & 1 else 2
                if f & 8:
                    cps.p += 2
                elif f & 0x40:
                    cps.p += 4
                elif f & 0x80:
                    cps.p += 8
                more = bool(f & 0x20)
                hasins = hasins or bool(f & 0x100)
            comp = d[start:cps.p]
            bbox = struct.unpack_from(">4h", d, bbs.p)
            bbs.p += 8
            g_ = struct.pack(">h4h", -1, *bbox) + comp
            if hasins:
                il = gls.u255()
                g_ += struct.pack(">H", il) + d[ins.p:ins.p + il]
                ins.p += il
            xmins.append(bbox[0])
        if len(g_) % 4:
            g_ += b"\0" * (4 - len(g_) % 4)
        out.append(g_)
    loca = b""
    glyf = b""
    for g_ in out:
        loca += struct.pack(">I", len(glyf))
        glyf += g_
    loca += struct.pack(">I", len(glyf))
    return glyf, loca, xmins


def rebuild_hmtx(d, n_hm, n_glyphs, xmins):
    r = R(d)
    f = r.u8()
    adv = [r.u16() for _ in range(n_hm)]
    lsb = [r.i16() for _ in range(n_hm)] if not f & 1 else xmins[:n_hm]
    rest = [r.i16() for _ in range(n_glyphs - n_hm)] if not f & 2 else xmins[n_hm:]
    o = b""
    for a, l in zip(adv, lsb):
        o += struct.pack(">Hh", a, l)
    for l in rest:
        o += struct.pack(">h", l)
    return o


def cs(b):
    b = b + b"\0" * (-len(b) % 4)
    return sum(struct.unpack(">%dI" % (len(b) // 4), b)) & 0xFFFFFFFF


def convert(src, dst):
    w = open(src, "rb").read()
    r = R(w)
    assert w[:4] == b"wOF2"
    r.p = 4
    flavor = r.u32()
    r.u32()
    nt = r.u16()
    r.u16()
    r.u32()
    comp_len = r.u32()
    r.p = 48
    ents = []
    for _ in range(nt):
        fl = r.u8()
        idx = fl & 63
        ver = fl >> 6
        if idx == 63:
            tag = w[r.p:r.p + 4].decode("latin1")
            r.p += 4
        else:
            tag = TAGS[idx]
        ol = r.base128()
        tr = (ver == 0) if tag in ("glyf", "loca") else (ver != 0)
        tl = r.base128() if tr else ol
        ents.append([tag, ol, tl, tr])
    raw = brotli(w[r.p:r.p + comp_len])
    p = 0
    tabs = {}
    for e in ents:
        tabs[e[0]] = raw[p:p + e[2]]
        p += e[2]
    xmins = None
    if any(e[0] == "glyf" and e[3] for e in ents):
        glyf, loca, xmins = rebuild_glyf(tabs["glyf"])
        tabs["glyf"] = glyf
        tabs["loca"] = loca
        h = bytearray(tabs["head"])
        struct.pack_into(">h", h, 50, 1)
        tabs["head"] = bytes(h)
    if any(e[0] == "hmtx" and e[3] for e in ents):
        nh = struct.unpack_from(">H", tabs["hhea"], 34)[0]
        ng = struct.unpack_from(">H", tabs["maxp"], 4)[0]
        tabs["hmtx"] = rebuild_hmtx(tabs["hmtx"], nh, ng, xmins)
    h = bytearray(tabs["head"])
    struct.pack_into(">I", h, 8, 0)
    tabs["head"] = bytes(h)
    names = sorted(tabs)
    n = len(names)
    sr = 1
    while sr * 2 <= n:
        sr *= 2
    hdr = struct.pack(">IHHHH", flavor, n, sr * 16, sr.bit_length() - 1, n * 16 - sr * 16)
    off = 12 + 16 * n
    dirb = b""
    body = b""
    head_off = 0
    for t in names:
        d = tabs[t]
        if t == "head":
            head_off = off + len(body)
        dirb += t.encode("latin1") + struct.pack(">III", cs(d), off + len(body), len(d))
        body += d + b"\0" * (-len(d) % 4)
    font = bytearray(hdr + dirb + body)
    adj = (0xB1B0AFBA - cs(bytes(font))) & 0xFFFFFFFF
    struct.pack_into(">I", font, head_off + 8, adj)
    open(dst, "wb").write(font)


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2])
