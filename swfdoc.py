#!/usr/bin/env python3
"""Lecteur minimal de SWF (sous-ensemble utilisé par les pages Doc88/DocBox .ebt).

Tags gérés : DefineShape/2/3, DefineText/2, DefineFont2/3, PlaceObject2, DefineBitsLossless/2,
DefineBitsJPEG2/3.  Coordonnées en twips (1/20 pt), axe Y vers le bas (convention SWF).
"""
import struct
import zlib

# --------------------------------------------------------------------------- bits
class BR:
    def __init__(self, buf, pos=0):
        self.b, self.p, self.bit = buf, pos, 0

    def align(self):
        if self.bit:
            self.p += 1
            self.bit = 0

    def ub(self, n):
        v = 0
        for _ in range(n):
            byte = self.b[self.p] if self.p < len(self.b) else 0
            v = (v << 1) | ((byte >> (7 - self.bit)) & 1)
            self.bit += 1
            if self.bit == 8:
                self.bit = 0
                self.p += 1
        return v

    def sb(self, n):
        v = self.ub(n)
        if n and v >> (n - 1):
            v -= 1 << n
        return v

    def fb(self, n):
        return self.sb(n) / 65536.0

    def u8(self):
        self.align(); v = self.b[self.p]; self.p += 1; return v

    def u16(self):
        self.align(); v = struct.unpack_from('<H', self.b, self.p)[0]; self.p += 2; return v

    def s16(self):
        self.align(); v = struct.unpack_from('<h', self.b, self.p)[0]; self.p += 2; return v

    def u32(self):
        self.align(); v = struct.unpack_from('<I', self.b, self.p)[0]; self.p += 4; return v

    def cstr(self):
        self.align()
        e = self.b.index(b'\0', self.p)
        s = self.b[self.p:e].decode('utf-8', 'replace')
        self.p = e + 1
        return s


def read_rect(br):
    n = br.ub(5)
    r = (br.sb(n), br.sb(n), br.sb(n), br.sb(n))   # xmin xmax ymin ymax
    br.align()
    return r


class M:
    """Matrice affine : x' = a*x + c*y + tx ; y' = b*x + d*y + ty."""
    __slots__ = ('a', 'b', 'c', 'd', 'tx', 'ty')

    def __init__(self, a=1.0, b=0.0, c=0.0, d=1.0, tx=0.0, ty=0.0):
        self.a, self.b, self.c, self.d, self.tx, self.ty = a, b, c, d, tx, ty

    def mul(self, o):
        """self ∘ o : applique o d'abord, puis self."""
        return M(self.a * o.a + self.c * o.b, self.b * o.a + self.d * o.b,
                 self.a * o.c + self.c * o.d, self.b * o.c + self.d * o.d,
                 self.a * o.tx + self.c * o.ty + self.tx, self.b * o.tx + self.d * o.ty + self.ty)

    def apply(self, x, y):
        return self.a * x + self.c * y + self.tx, self.b * x + self.d * y + self.ty

    def t(self):
        return (self.a, self.b, self.c, self.d, self.tx, self.ty)


def read_matrix(br):
    br.align()
    sx = sy = 1.0
    r0 = r1 = 0.0
    if br.ub(1):
        n = br.ub(5); sx = br.fb(n); sy = br.fb(n)
    if br.ub(1):
        n = br.ub(5); r0 = br.fb(n); r1 = br.fb(n)
    n = br.ub(5)
    tx = br.sb(n); ty = br.sb(n)
    br.align()
    return M(sx, r0, r1, sy, tx, ty)


def read_cxform(br, alpha=True):
    br.align()
    has_add = br.ub(1); has_mul = br.ub(1); n = br.ub(4)
    mul = [256, 256, 256, 256]; add = [0, 0, 0, 0]
    k = 4 if alpha else 3
    if has_mul:
        for i in range(k): mul[i] = br.sb(n)
    if has_add:
        for i in range(k): add[i] = br.sb(n)
    br.align()
    return mul, add

# --------------------------------------------------------------------------- tags
def iter_tags(buf, pos):
    while pos + 2 <= len(buf):
        h = struct.unpack_from('<H', buf, pos)[0]
        code, ln = h >> 6, h & 0x3f
        pos += 2
        if ln == 0x3f:
            ln = struct.unpack_from('<I', buf, pos)[0]; pos += 4
        yield code, buf[pos:pos + ln]
        pos += ln
        if code == 0:
            break


def swf_body_offset(dec):
    """Offset des tags d'un h.ebt décompressé (SWF complet 'FWS')."""
    nb = dec[8] >> 3
    return 8 + (5 + 4 * nb + 7) // 8 + 4


PAGE_TAG_OFFSET = 10   # pages : 10 octets (ff 09 + 2 u32) avant les tags

# --------------------------------------------------------------------------- shapes
def _read_color(br, rgba):
    r, g, b = br.u8(), br.u8(), br.u8()
    a = br.u8() if rgba else 255
    return (r, g, b, a)


def _read_gradient(br, rgba, focal=False):
    br.align()
    br.ub(2); br.ub(2); n = br.ub(4)
    recs = []
    for _ in range(n):
        ratio = br.u8(); recs.append((ratio, _read_color(br, rgba)))
    if focal:
        br.s16()
    return recs


def _read_fillstyles(br, ver):
    n = br.u8()
    if n == 0xff and ver >= 2:
        n = br.u16()
    out = []
    for _ in range(n):
        t = br.u8()
        if t == 0x00:
            out.append(('solid', _read_color(br, ver >= 3)))
        elif t in (0x10, 0x12, 0x13):
            m = read_matrix(br)
            out.append(('grad', t, m, _read_gradient(br, ver >= 3, t == 0x13)))
        elif 0x40 <= t <= 0x43:
            bid = br.u16(); m = read_matrix(br)
            out.append(('bitmap', t, bid, m))
        else:
            raise ValueError(f'fill style {t:#x} inconnu')
    return out


def _read_linestyles(br, ver):
    n = br.u8()
    if n == 0xff:
        n = br.u16()
    out = []
    for _ in range(n):
        w = br.u16(); c = _read_color(br, ver >= 3)
        out.append((w, c))
    return out


def read_shape_records(br, nf, nl, ver, fills, lines):
    """Retourne une liste de groupes : dict(fills, lines, edges).
    edge = (f0, f1, ls, x0, y0, seg) ; seg = ('L', x, y) | ('Q', cx, cy, x, y)."""
    groups = []
    edges = []
    x = y = 0
    f0 = f1 = ls = 0
    while True:
        if br.ub(1):  # edge
            if br.ub(1):  # straight
                n = br.ub(4) + 2
                if br.ub(1):
                    dx = br.sb(n); dy = br.sb(n)
                elif br.ub(1):
                    dx = 0; dy = br.sb(n)
                else:
                    dx = br.sb(n); dy = 0
                edges.append((f0, f1, ls, x, y, ('L', x + dx, y + dy)))
                x += dx; y += dy
            else:
                n = br.ub(4) + 2
                cdx = br.sb(n); cdy = br.sb(n); adx = br.sb(n); ady = br.sb(n)
                cx, cy = x + cdx, y + cdy
                edges.append((f0, f1, ls, x, y, ('Q', cx, cy, cx + adx, cy + ady)))
                x, y = cx + adx, cy + ady
        else:
            flags = br.ub(5)
            if flags == 0:
                break
            new_styles, st_line, st_f1, st_f0, st_move = (flags >> 4) & 1, (flags >> 3) & 1, (flags >> 2) & 1, (flags >> 1) & 1, flags & 1
            if st_move:
                n = br.ub(5); x = br.sb(n); y = br.sb(n)
            if st_f0: f0 = br.ub(nf)
            if st_f1: f1 = br.ub(nf)
            if st_line: ls = br.ub(nl)
            if new_styles:
                groups.append(dict(fills=fills, lines=lines, edges=edges))
                edges = []
                br.align()
                fills = _read_fillstyles(br, ver); lines = _read_linestyles(br, ver)
                nf = br.ub(4); nl = br.ub(4)
                f0 = f1 = ls = 0
    groups.append(dict(fills=fills, lines=lines, edges=edges))
    br.align()
    return groups


def parse_define_shape(data, ver):
    br = BR(data)
    sid = br.u16()
    bounds = read_rect(br)
    fills = _read_fillstyles(br, ver)
    lines = _read_linestyles(br, ver)
    nf = br.ub(4); nl = br.ub(4)
    groups = read_shape_records(br, nf, nl, ver, fills, lines)
    return sid, dict(bounds=bounds, groups=groups)


def parse_glyph_shape(data, pos):
    br = BR(data, pos)
    nf = br.ub(4); nl = br.ub(4)
    groups = read_shape_records(br, nf, nl, 3, [], [])
    return groups[0]['edges']

# --------------------------------------------------------------------------- fonts
def parse_define_font(code, data):
    br = BR(data)
    fid = br.u16()
    flags = br.u8()
    br.u8()  # language
    nl = br.u8()
    name = data[br.p:br.p + nl].rstrip(b'\0').decode('utf-8', 'replace'); br.p += nl
    n = br.u16()
    wide_off, wide_codes, layout = flags & 8, flags & 4, flags & 0x80
    base = br.p
    offs = [br.u32() if wide_off else br.u16() for _ in range(n)]
    code_off = (br.u32() if wide_off else br.u16()) if n or True else 0
    glyphs = []
    for i in range(n):
        try:
            glyphs.append(parse_glyph_shape(data, base + offs[i]))
        except Exception:
            glyphs.append([])
    cb = BR(data, base + code_off)
    codes = [cb.u16() if wide_codes else cb.u8() for _ in range(n)]
    adv = []
    ascent = descent = 0
    if layout:
        ascent = cb.s16(); descent = cb.s16(); cb.s16()
        adv = [cb.s16() for _ in range(n)]
    return fid, dict(name=name, flags=flags, glyphs=glyphs, codes=codes, adv=adv,
                     em=20480.0 if code == 75 else 1024.0, ascent=ascent, descent=descent,
                     ansi=bool(flags & 0x10), sjis=bool(flags & 0x40))

# --------------------------------------------------------------------------- text
def parse_define_text(code, data):
    ver = 2 if code == 33 else 1
    br = BR(data)
    cid = br.u16()
    bounds = read_rect(br)
    matrix = read_matrix(br)
    gbits = br.u8(); abits = br.u8()
    recs = []
    font = None; height = 0; color = (0, 0, 0, 255); xo = yo = None
    while True:
        br.align()
        first = br.b[br.p] if br.p < len(br.b) else 0
        if first == 0:
            break
        br.p += 1
        if first & 0x80:   # style record
            if first & 8: font = br.u16()
            if first & 4: color = _read_color(br, ver == 2)
            if first & 1: xo = br.s16()
            if first & 2: yo = br.s16()
            if first & 8: height = br.u16()
            recs.append(('style', font, color, xo, yo, height))
            xo = yo = None
        else:
            cnt = first & 0x7f
            gl = []
            for _ in range(cnt):
                gi = br.ub(gbits); ad = br.sb(abits); gl.append((gi, ad))
            recs.append(('glyphs', gl))
    return cid, dict(bounds=bounds, matrix=matrix, recs=recs)


def text_runs(txt, fonts):
    """Itère (x, y, height, font_id, glyph_index, advance, color) dans l'espace du texte."""
    x = y = 0
    font = None; height = 0; color = (0, 0, 0, 255)
    for r in txt['recs']:
        if r[0] == 'style':
            _, f, c, xo, yo, h = r
            if f is not None: font = f
            color = c
            if xo is not None: x = xo
            if yo is not None: y = yo
            if h: height = h
        else:
            for gi, ad in r[1]:
                yield x, y, height, font, gi, ad, color
                x += ad

# --------------------------------------------------------------------------- bitmaps
def _row_pad(w, bpp):
    return (w * bpp + 3) // 4 * 4


def decode_bitmap(code, data):
    """Retourne (cid, PIL.Image RGBA)."""
    from PIL import Image
    import io
    import numpy as np
    cid = struct.unpack_from('<H', data, 0)[0]
    if code in (20, 36):
        fmt, w, h = data[2], *struct.unpack_from('<HH', data, 3)
        pos = 7
        has_alpha = code == 36
        if fmt == 3:
            n = data[pos] + 1; pos += 1
            raw = zlib.decompress(data[pos:])
            cm = 4 if has_alpha else 3
            table = np.zeros((256, 4), np.uint8)
            table[:, 3] = 255
            pal = np.frombuffer(raw[:n * cm], np.uint8).reshape(n, cm)
            table[:n, :cm] = pal
            if n < 256:
                table[n:, :] = 0 if has_alpha else (0, 0, 0, 255)
            stride = _row_pad(w, 1)
            pix = np.frombuffer(raw[n * cm:n * cm + h * stride].ljust(h * stride, b'\0'), np.uint8)
            idx = pix.reshape(h, stride)[:, :w]
            arr = table[idx]
        elif fmt == 5:
            raw = zlib.decompress(data[pos:])
            a4 = np.frombuffer(raw[:w * h * 4].ljust(w * h * 4, b'\0'), np.uint8).reshape(h, w, 4)
            if has_alpha:   # ARGB prémultiplié -> RGBA
                al = a4[:, :, 0].astype(np.uint32)
                den = np.maximum(al, 1)
                rgb = np.minimum(255, a4[:, :, 1:4].astype(np.uint32) * 255 // den[:, :, None])
                rgb = np.where((al > 0)[:, :, None], rgb, a4[:, :, 1:4]).astype(np.uint8)
                arr = np.dstack([rgb, a4[:, :, 0]])
            else:           # pad R G B
                arr = np.dstack([a4[:, :, 1:4], np.full((h, w), 255, np.uint8)])
        elif fmt == 4:
            raw = zlib.decompress(data[pos:])
            stride = _row_pad(w, 2)
            v = np.frombuffer(raw[:h * stride].ljust(h * stride, b'\0'), '>u2').reshape(h, stride // 2)[:, :w].astype(np.uint32)
            r = ((v >> 10) & 31) * 255 // 31
            g = ((v >> 5) & 31) * 255 // 31
            b = (v & 31) * 255 // 31
            arr = np.dstack([r, g, b, np.full((h, w), 255, np.uint32)]).astype(np.uint8)
        else:
            raise ValueError(f'bitmap format {fmt}')
        return cid, Image.fromarray(np.ascontiguousarray(arr), 'RGBA')
    if code in (21, 35):
        if code == 35:
            alpha_off = struct.unpack_from('<I', data, 2)[0]
            img = data[6:6 + alpha_off]
            alpha = zlib.decompress(data[6 + alpha_off:])
        else:
            img = data[2:]; alpha = None
        # nettoyer les marqueurs erronés FFD9 FFD8 en tête
        if img[:4] == b'\xff\xd9\xff\xd8':
            img = img[4:]
        im = Image.open(io.BytesIO(img)).convert('RGBA')
        if alpha and len(alpha) >= im.width * im.height:
            im.putalpha(Image.frombytes('L', im.size, alpha[:im.width * im.height]))
        return cid, im
    raise ValueError(code)

# --------------------------------------------------------------------------- PlaceObject2
def parse_place2(data):
    br = BR(data)
    fl = br.u8()
    depth = br.u16()
    o = dict(depth=depth, move=bool(fl & 1), cid=None, matrix=None, cx=None, clip=None)
    if fl & 2: o['cid'] = br.u16()
    if fl & 4: o['matrix'] = read_matrix(br)
    if fl & 8: o['cx'] = read_cxform(br, True)
    if fl & 16: br.u16()
    if fl & 32: br.cstr()
    if fl & 64: o['clip'] = br.u16()
    return o

# --------------------------------------------------------------------------- document
class Fonts(dict):
    pass


def load_fonts(h_dec):
    fonts = Fonts()
    for code, data in iter_tags(h_dec, swf_body_offset(h_dec)):
        if code in (48, 75):
            fid, f = parse_define_font(code, data)
            fonts[fid] = f
    return fonts


def load_page(dec):
    """Retourne dict(shapes, texts, bitmaps, display(list d'ordre), ...)."""
    shapes, texts, bitmaps, placements = {}, {}, {}, []
    for code, data in iter_tags(dec, PAGE_TAG_OFFSET):
        if code in (2, 22, 32):
            sid, s = parse_define_shape(data, {2: 1, 22: 2, 32: 3}[code]); shapes[sid] = s
        elif code in (11, 33):
            cid, t = parse_define_text(code, data); texts[cid] = t
        elif code in (20, 36, 21, 35):
            try:
                cid, im = decode_bitmap(code, data); bitmaps[cid] = im
            except Exception as e:  # image illisible : on ignore
                bitmaps[struct.unpack_from('<H', data, 0)[0]] = None
        elif code == 26:
            placements.append(parse_place2(data))
    return dict(shapes=shapes, texts=texts, bitmaps=bitmaps, placements=placements)


def display_list(page):
    """Applique les PlaceObject2 -> liste triée par profondeur."""
    dl = {}
    for p in page['placements']:
        d = p['depth']
        if p['cid'] is not None and not p['move']:
            dl[d] = dict(cid=p['cid'], matrix=p['matrix'] or M(), cx=p['cx'], clip=p['clip'])
        elif d in dl:
            if p['matrix'] is not None: dl[d]['matrix'] = p['matrix']
            if p['cx'] is not None: dl[d]['cx'] = p['cx']
            if p['cid'] is not None: dl[d]['cid'] = p['cid']
        else:
            dl[d] = dict(cid=p['cid'], matrix=p['matrix'] or M(), cx=p['cx'], clip=p['clip'])
    return [(d, dl[d]) for d in sorted(dl)]


def font_char(font, gi):
    if not font or gi >= len(font['codes']):
        return ''
    c = font['codes'][gi]
    if font['sjis'] and not (font['flags'] & 4):
        return chr(c)
    return chr(c) if c else ''


def page_text(page, fonts, tol=0.35):
    """Texte de la page : liste de lignes (y, [(x, char, hauteur)])."""
    glyphs = []
    for d, o in display_list(page):
        t = page['texts'].get(o['cid'])
        if not t:
            continue
        m = o['matrix'].mul(t['matrix'])
        for x, y, h, fid, gi, ad, col in text_runs(t, fonts):
            f = fonts.get(fid)
            ch = font_char(f, gi)
            px, py = m.apply(x, y)
            hh = abs(h * m.d) if h else 0
            glyphs.append((py, px, ch, hh, ad * m.a))
    glyphs.sort(key=lambda g: (round(g[0] / 20.0), g[1]))
    lines = []
    for py, px, ch, hh, adv in glyphs:
        if lines and abs(lines[-1]['y'] - py) <= max(60, hh * tol):
            ln = lines[-1]
        else:
            ln = dict(y=py, g=[])
            lines.append(ln)
        ln['g'].append((px, ch, hh, adv))
    out = []
    for ln in lines:
        ln['g'].sort(key=lambda g: g[0])
        s = ''
        prev = None
        for px, ch, hh, adv in ln['g']:
            if prev is not None:
                gap = px - (prev[0] + prev[3])
                if gap > 0.3 * max(prev[2], hh, 1):
                    s += ' '
            s += ch
            prev = (px, ch, hh, adv)
        out.append((ln['y'], s))
    return out
