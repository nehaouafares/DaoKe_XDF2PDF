#!/usr/bin/env python3
"""Convertit un .xdf DocBox (ou un dossier de cache DocBox) en PDF + texte brut.

Usage : python xdf2pdf.py entrée [sortie.pdf]
  entrée : fichier .xdf, ou dossier de document du cache (contient docSrc.xml et 1\\*.ebt ...)
Produit  <sortie>.pdf  (formes et glyphes vectoriels, images, couche de texte invisible
         pour la recherche/copie)  et  <sortie>.txt  (texte des pages).
Dépend de : reportlab, Pillow ; modules locaux swfdoc.py et xdf_extract.py.
"""
import os
import re
import sys
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swfdoc as S
import xdf_extract as X
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

try:
    from reportlab.pdfgen.canvas import FILL_EVEN_ODD, FILL_NON_ZERO
except ImportError:  # anciennes versions
    FILL_EVEN_ODD, FILL_NON_ZERO = 0, 1

pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))

# formatage de nombres plus rapide que le repli Python de reportlab (rl_accel absent)
def fast_fp_str(*a):
    if len(a) == 1 and isinstance(a[0], (list, tuple)):
        a = a[0]
    return ' '.join([str(round(x, 3)) if x == x else '0' for x in a])


try:
    import reportlab.pdfgen.pathobject as _po
    import reportlab.pdfgen.textobject as _to
    import reportlab.pdfgen.canvas as _cv
    for _m in (_po, _to, _cv):
        if hasattr(_m, 'fp_str'):
            _m.fp_str = fast_fp_str
except Exception:
    pass



# ------------------------------------------------------------------ chargement
def _numkey(s):
    return int(s) if s.isdigit() else 10 ** 9


def load_xdf(path):
    _, _, entries, _ = X.parse(open(path, 'rb').read())
    docsrc, groups = None, {}
    for e in entries:
        data, ok = X.decode(e)
        parts = e['name'].replace('/', '\\').split('\\')
        if parts[-1] == 'docSrc.xml':
            docsrc = data.decode('utf-8', 'replace')
        elif parts[-1].endswith('.ebt'):
            g = groups.setdefault(parts[-2] if len(parts) > 1 else '', dict(h=None, pages={}))
            stem = parts[-1][:-4]
            dec = X.unwrap_ebt(data)
            if stem == 'h':
                g['h'] = dec
            else:
                g['pages'][int(stem)] = dec
    return docsrc, groups


def load_dir(path):
    docsrc, groups = None, {}
    p = os.path.join(path, 'docSrc.xml')
    if os.path.exists(p):
        docsrc = open(p, 'rb').read().decode('utf-8', 'replace')
    for sub in sorted(os.listdir(path), key=_numkey):
        d = os.path.join(path, sub)
        if not os.path.isdir(d):
            continue
        g = dict(h=None, pages={})
        for fn in os.listdir(d):
            if fn.endswith('.ebt'):
                raw = open(os.path.join(d, fn), 'rb').read()
                if not raw:
                    continue
                dec = X.unwrap_ebt(raw)
                stem = fn[:-4]
                if stem == 'h':
                    g['h'] = dec
                else:
                    g['pages'][int(stem)] = dec
        if g['h'] or g['pages']:
            groups[sub] = g
    return docsrc, groups


# ------------------------------------------------------------------ géométrie
def _rev(x0, y0, seg):
    if seg[0] == 'L':
        return seg[1], seg[2], ('L', x0, y0)
    return seg[3], seg[4], ('Q', seg[1], seg[2], x0, y0)


def _end(seg):
    return (seg[1], seg[2]) if seg[0] == 'L' else (seg[3], seg[4])


def chain(segs):
    """Chaîne des segments (x0,y0,seg) en contours."""
    by_start = {}
    for i, (x0, y0, seg) in enumerate(segs):
        by_start.setdefault((x0, y0), []).append(i)
    used = [False] * len(segs)
    contours = []
    for i in range(len(segs)):
        if used[i]:
            continue
        used[i] = True
        cur = [segs[i]]
        start = (segs[i][0], segs[i][1])
        end = _end(segs[i][2])
        while end != start:
            nxt = None
            for j in by_start.get(end, ()):
                if not used[j]:
                    nxt = j
                    break
            if nxt is None:
                break
            used[nxt] = True
            cur.append(segs[nxt])
            end = _end(segs[nxt][2])
        contours.append(cur)
    return contours


def fill_contours(edges, idx=None):
    segs = []
    for f0, f1, ls, x0, y0, seg in edges:
        if idx is None:
            if f1:
                segs.append((x0, y0, seg))
            if f0:
                segs.append(_rev(x0, y0, seg))
        else:
            if f1 == idx:
                segs.append((x0, y0, seg))
            if f0 == idx:
                segs.append(_rev(x0, y0, seg))
    return chain(segs)


def build_path(c, contours, m, close=True):
    p = c.beginPath()
    for cont in contours:
        x0, y0, _ = cont[0]
        px, py = m.apply(x0, y0)
        p.moveTo(px, py)
        cx0, cy0 = px, py
        for x0, y0, seg in cont:
            if seg[0] == 'L':
                ex, ey = m.apply(seg[1], seg[2])
                p.lineTo(ex, ey)
                cx0, cy0 = ex, ey
            else:
                qx, qy = m.apply(seg[1], seg[2])
                ex, ey = m.apply(seg[3], seg[4])
                p.curveTo(cx0 + 2 / 3 * (qx - cx0), cy0 + 2 / 3 * (qy - cy0),
                          ex + 2 / 3 * (qx - ex), ey + 2 / 3 * (qy - ey), ex, ey)
                cx0, cy0 = ex, ey
        if close:
            p.close()
    return p


def has_area(contours):
    return any(len(c) >= 2 for c in contours)


def _cx(color, cx):
    r, g, b, a = color
    if cx:
        mul, add = cx
        r = max(0, min(255, (r * mul[0]) // 256 + add[0]))
        g = max(0, min(255, (g * mul[1]) // 256 + add[1]))
        b = max(0, min(255, (b * mul[2]) // 256 + add[2]))
        a = max(0, min(255, (a * mul[3]) // 256 + add[3]))
    return r / 255.0, g / 255.0, b / 255.0, a / 255.0


def set_fill(c, color, cx):
    r, g, b, a = _cx(color, cx)
    c.setFillColorRGB(r, g, b, alpha=a)


# ------------------------------------------------------------------ rendu
class Renderer:
    def __init__(self, c, page, fonts, base, text_layer=True):
        self.c, self.page, self.fonts, self.base = c, page, fonts, base
        self.text_layer = text_layer
        self.glyph_cache = {}
        self.stats = dict(missing_glyph=0, grad=0, img=0)

    def glyph_contours(self, fid, gi):
        key = (fid, gi)
        if key not in self.glyph_cache:
            f = self.fonts.get(fid)
            edges = f['glyphs'][gi] if f and gi < len(f['glyphs']) else []
            self.glyph_cache[key] = fill_contours(edges, None) if edges else []
        return self.glyph_cache[key]

    def draw_shape(self, shape, m, cx, clip_only=False):
        c = self.c
        for grp in shape['groups']:
            edges = grp['edges']
            if clip_only:
                conts = fill_contours(edges, None)
                if has_area(conts):
                    p = build_path(c, conts, m)
                    c.clipPath(p, stroke=0, fill=0, fillMode=FILL_NON_ZERO)
                return
            for i, fs in enumerate(grp['fills'], start=1):
                conts = fill_contours(edges, i)
                if not has_area(conts):
                    continue
                p = build_path(c, conts, m)
                if fs[0] == 'solid':
                    set_fill(c, fs[1], cx)
                    c.drawPath(p, stroke=0, fill=1, fillMode=FILL_EVEN_ODD)
                elif fs[0] == 'grad':
                    self.stats['grad'] += 1
                    col = fs[3][len(fs[3]) // 2][1] if fs[3] else (128, 128, 128, 255)
                    set_fill(c, col, cx)
                    c.drawPath(p, stroke=0, fill=1, fillMode=FILL_EVEN_ODD)
                else:  # bitmap
                    im = self.page['bitmaps'].get(fs[2])
                    if im is None:
                        continue
                    self.stats['img'] += 1
                    c.saveState()
                    c.setFillAlpha(1.0)   # l'alpha d'un texte précédent (ex. OCR transparent) persisterait sinon
                    c.clipPath(p, stroke=0, fill=0, fillMode=FILL_EVEN_ODD)
                    t = m.mul(fs[3]).mul(S.M(1, 0, 0, -1, 0, im.height))
                    c.transform(*t.t())
                    c.drawImage(ImageReader(im), 0, 0, im.width, im.height, mask='auto')
                    c.restoreState()
            # traits
            scale = math.sqrt(abs(m.a * m.d - m.b * m.c))
            for j, (w, col) in enumerate(grp['lines'], start=1):
                segs = [(x0, y0, seg) for f0, f1, ls, x0, y0, seg in edges if ls == j]
                if not segs:
                    continue
                p = c.beginPath()
                last = None
                for x0, y0, seg in segs:
                    if last != (x0, y0):
                        px, py = m.apply(x0, y0)
                        p.moveTo(px, py)
                    cur = m.apply(x0, y0)
                    if seg[0] == 'L':
                        ex, ey = m.apply(seg[1], seg[2])
                        p.lineTo(ex, ey)
                    else:
                        qx, qy = m.apply(seg[1], seg[2])
                        ex, ey = m.apply(seg[3], seg[4])
                        p.curveTo(cur[0] + 2 / 3 * (qx - cur[0]), cur[1] + 2 / 3 * (qy - cur[1]),
                                  ex + 2 / 3 * (qx - ex), ey + 2 / 3 * (qy - ey), ex, ey)
                    last = _end(seg)
                r, g, b, a = _cx(col, cx)
                c.setStrokeColorRGB(r, g, b, alpha=a)
                c.setLineWidth(max(w * scale, 0.25))
                c.setLineCap(1)
                c.setLineJoin(1)
                c.drawPath(p, stroke=1, fill=0)

    def draw_text(self, txt, mp, cx, invisible):
        c = self.c
        mt = mp.mul(txt['matrix'])
        sc = math.sqrt(abs(mt.a * mt.d - mt.b * mt.c))
        run = None

        def flush(r):
            text = r['text']
            if text.strip():
                px, py = mt.apply(r['x0'], r['y'])
                invisible.append((px, py, r['h'] * sc, text, (r['xend'] - r['x0']) * sc))

        for x, y, h, fid, gi, ad, col in S.text_runs(txt, self.fonts):
            f = self.fonts.get(fid)
            if not f or not h:
                continue
            s = h / f['em']
            mg = mt.mul(S.M(s, 0, 0, s, x, y))
            conts = self.glyph_contours(fid, gi)
            if conts and has_area(conts):
                set_fill(c, col, cx)
                c.drawPath(build_path(c, conts, mg), stroke=0, fill=1, fillMode=FILL_NON_ZERO)
            elif not conts:
                self.stats['missing_glyph'] += 1
            ch = S.font_char(f, gi)
            key = (y, fid, h)
            if run is not None and run['key'] == key and x == run['xend']:
                run['text'] += ch
                run['xend'] = x + ad
            else:
                if run:
                    flush(run)
                run = dict(key=key, x0=x, y=y, h=h, text=ch, xend=x + ad)
        if run:
            flush(run)

    def render(self):
        c = self.c
        invisible = []
        clip_stack = []
        for depth, o in S.display_list(self.page):
            while clip_stack and depth > clip_stack[-1]:
                c.restoreState()
                clip_stack.pop()
            mp = self.base.mul(o['matrix'])
            cid = o['cid']
            if o['clip'] is not None:
                c.saveState()
                clip_stack.append(o['clip'])
                sh = self.page['shapes'].get(cid)
                if sh:
                    self.draw_shape(sh, mp, o['cx'], clip_only=True)
                continue
            if cid in self.page['shapes']:
                self.draw_shape(self.page['shapes'][cid], mp, o['cx'])
            elif cid in self.page['texts']:
                self.draw_text(self.page['texts'][cid], mp, o['cx'], invisible)
        while clip_stack:
            c.restoreState()
            clip_stack.pop()
        # couche de texte invisible (recherche / copier-coller) : une chaîne par segment de texte
        if invisible and self.text_layer:
            t = c.beginText()
            t.setTextRenderMode(3)
            for px, py, size, text, width in invisible:
                font = 'Helvetica' if text.isascii() else 'STSong-Light'
                size = max(size, 1)
                natural = pdfmetrics.stringWidth(text, font, size)
                t.setFont(font, size)
                t.setHorizScale(min(500, max(10, 100.0 * width / natural)) if natural > 0 and width > 0 else 100)
                t.setTextOrigin(px, py)
                t.textOut(text)
            c.drawText(t)


def page_size(page):
    dl = S.display_list(page)
    for d, o in dl:
        sh = page['shapes'].get(o['cid'])
        if sh:
            x0, x1, y0, y1 = sh['bounds']
            return x0, y0, x1, y1
    return 0, 0, 11900, 16840


class Cancelled(Exception):
    pass


def _meta(docsrc):
    title, pagecount = '', None
    if docsrc:
        m = re.search(r'<p_name>\s*<!\[CDATA\[(.*?)\]\]>', docsrc, re.S)
        title = m.group(1).strip() if m else ''
        m = re.search(r'<p_pagecount>\s*<!\[CDATA\[(\d+)\]\]>', docsrc)
        pagecount = int(m.group(1)) if m else None
    return title, pagecount


def inspect(src):
    """Infos rapides (sans rendu) : dict(title, pagecount, pages)."""
    docsrc, npages = None, 0
    if src.lower().endswith('.xdf'):
        _, _, entries, _ = X.parse(open(src, 'rb').read())
        for e in entries:
            nm = e['name'].replace('/', '\\').split('\\')[-1]
            if nm == 'docSrc.xml':
                docsrc = X.decode(e)[0].decode('utf-8', 'replace')
            elif nm.endswith('.ebt') and nm != 'h.ebt':
                npages += 1
    else:
        p = os.path.join(src, 'docSrc.xml')
        if os.path.exists(p):
            docsrc = open(p, 'rb').read().decode('utf-8', 'replace')
        for sub in os.listdir(src):
            d = os.path.join(src, sub)
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    if fn.endswith('.ebt') and fn != 'h.ebt' and os.path.getsize(os.path.join(d, fn)) > 0:
                        npages += 1
    title, pagecount = _meta(docsrc)
    return dict(title=title, pagecount=pagecount, pages=npages)


def looks_like_doc_dir(path):
    """Vrai si `path` est un dossier de document du cache DocBox (docSrc.xml ou sous-dossier avec .ebt)."""
    if os.path.isfile(os.path.join(path, 'docSrc.xml')):
        return True
    try:
        for sub in os.listdir(path):
            d = os.path.join(path, sub)
            if os.path.isdir(d) and any(f.endswith('.ebt') for f in os.listdir(d)):
                return True
    except OSError:
        pass
    return False


def convert(src, out_pdf, progress=None, cancel=None, text_layer=True, write_txt=True, log=print):
    """Convertit `src` (.xdf ou dossier de cache) en PDF (+ .txt).

    progress(done, total, message) ; cancel : threading.Event ; log(str).
    Retourne dict(title, pages_done, pagecount, errors, stats, pdf, txt)."""
    docsrc, groups = load_xdf(src) if src.lower().endswith('.xdf') else load_dir(src)
    title, pagecount = _meta(docsrc)
    pages = []
    for folder, g in groups.items():
        fonts = S.load_fonts(g['h']) if g['h'] else {}
        for n, dec in g['pages'].items():
            pages.append((n, folder, fonts, dec))
    pages.sort(key=lambda t: t[0])
    if not pages:
        raise ValueError('aucune page .ebt trouvée dans ' + src)
    total = len(pages)
    c = canvas.Canvas(out_pdf, pageCompression=1)
    c.setTitle(title or os.path.basename(src))
    c.setCreator('xdf2pdf')
    text_out, errors = [], []
    totals = dict(missing_glyph=0, grad=0, img=0)
    for k, (n, folder, fonts, dec) in enumerate(pages):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if progress:
            progress(k, total, f'page {n}')
        try:
            page = S.load_page(dec)
            x0, y0, x1, y1 = page_size(page)
            w, h = (x1 - x0) / 20.0, (y1 - y0) / 20.0
            c.setPageSize((w, h))
            base = S.M(1 / 20.0, 0, 0, -1 / 20.0, -x0 / 20.0, y1 / 20.0)
            r = Renderer(c, page, fonts, base, text_layer=text_layer)
            r.render()
            for key in totals:
                totals[key] += r.stats[key]
            text_out.append(f'===== Page {n} =====\n' + '\n'.join(s for _, s in S.page_text(page, fonts)))
        except Exception as ex:  # page illisible : on garde une page vide et on continue
            errors.append((n, repr(ex)))
            log(f'  ! page {n} : {ex!r}')
            text_out.append(f'===== Page {n} =====\n[erreur de conversion : {ex!r}]')
        c.showPage()
    if progress:
        progress(total, total, 'écriture du PDF')
    c.save()
    txt_path = None
    if write_txt:
        txt_path = os.path.splitext(out_pdf)[0] + '.txt'
        open(txt_path, 'w', encoding='utf-8').write('\n\n'.join(text_out) + '\n')
    have = [n for n, *_ in pages]
    log(f'{src}\n  titre : {title!r}  pages converties : {total}'
        + (f' / {pagecount} (les autres ne sont pas dans la source)' if pagecount else '')
        + f'\n  pages : {have}\n  stats : {totals}\n  -> {out_pdf}' + (f' , {txt_path}' if txt_path else ''))
    return dict(title=title, pages_done=total, pagecount=pagecount, errors=errors,
                stats=totals, pdf=out_pdf, txt=txt_path)


def extract_text(src, out_txt, progress=None, cancel=None, log=print):
    """Extrait uniquement le texte des pages (sans rendu PDF)."""
    docsrc, groups = load_xdf(src) if src.lower().endswith('.xdf') else load_dir(src)
    title, pagecount = _meta(docsrc)
    pages = []
    for folder, g in groups.items():
        fonts = S.load_fonts(g['h']) if g['h'] else {}
        for n, dec in g['pages'].items():
            pages.append((n, fonts, dec))
    pages.sort(key=lambda t: t[0])
    if not pages:
        raise ValueError('aucune page .ebt trouvée dans ' + src)
    total, text_out = len(pages), []
    for k, (n, fonts, dec) in enumerate(pages):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if progress:
            progress(k, total, f'page {n}')
        try:
            page = S.load_page(dec)
            text_out.append(f'===== Page {n} =====\n' + '\n'.join(s for _, s in S.page_text(page, fonts)))
        except Exception as ex:
            log(f'  ! page {n} : {ex!r}')
            text_out.append(f'===== Page {n} =====\n[erreur d\'extraction : {ex!r}]')
    if progress:
        progress(total, total, 'écriture')
    open(out_txt, 'w', encoding='utf-8').write('\n\n'.join(text_out) + '\n')
    log(f'{src}\n  titre : {title!r}  pages : {total}' + (f' / {pagecount}' if pagecount else '') + f'\n  -> {out_txt}')
    return dict(title=title, pages_done=total, pagecount=pagecount, txt=out_txt)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    s = sys.argv[1]
    o = sys.argv[2] if len(sys.argv) > 2 else (os.path.splitext(s)[0] if s.lower().endswith('.xdf')
                                                 else s.rstrip('/\\')) + '.pdf'
    convert(s, o)
