#!/usr/bin/env python3
"""DocBox XDF -> PDF : interface graphique (tkinter).

Lancer :  python xdf_gui.py
Dépendances : reportlab, Pillow, numpy (voir requirements.txt) ; modules locaux
swfdoc.py, xdf_extract.py, xdf2pdf.py (dans le même dossier).

Sources acceptées : fichiers .xdf, dossiers de document du cache DocBox
(contenant docSrc.xml et 1\\N.ebt), ou le dossier racine du cache / un dossier de .xdf
(tous les documents trouvés sont ajoutés).
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xdf2pdf as P  # noqa: E402
import xdf_extract as X  # noqa: E402

APP_NAME = 'DocBox XDF → PDF by Nehaoua Fares (c) 2026'
CFG_PATH = os.path.join(os.path.expanduser('~'), '.docbox_xdf_gui.json')
DEFAULT_OUT = os.path.join(os.path.expanduser('~'), 'DocBox_PDF')


# ------------------------------------------------------------------ utilitaires
def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


def safe_name(s, maxlen=110):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', ' ', s).strip(' .')
    return re.sub(r'\s+', ' ', s)[:maxlen].strip() or 'document'


def open_path(path):
    try:
        if sys.platform.startswith('win'):
            os.startfile(path)  # noqa: S606
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])
    except Exception as ex:  # pragma: no cover
        messagebox.showerror(APP_NAME, f'Impossible d’ouvrir :\n{path}\n\n{ex}')


def collect_sources(paths):
    """Développe fichiers/dossiers en liste de sources (.xdf ou dossier de document)."""
    out = []
    for p in paths:
        p = os.path.normpath(p)
        if os.path.isfile(p) and p.lower().endswith('.xdf'):
            out.append(p)
        elif os.path.isdir(p):
            if P.looks_like_doc_dir(p):
                out.append(p)
                continue
            try:
                names = sorted(os.listdir(p), key=natural_key)
            except OSError:
                continue
            for n in names:
                q = os.path.join(p, n)
                if os.path.isfile(q) and n.lower().endswith('.xdf'):
                    out.append(q)
                elif os.path.isdir(q) and P.looks_like_doc_dir(q):
                    out.append(q)
    seen, res = set(), []
    for p in out:
        k = os.path.normcase(p)
        if k not in seen:
            seen.add(k)
            res.append(p)
    return res


# ------------------------------------------------------------------ application
class App:
    COLS = (('titre', 'Titre', 330), ('source', 'Source', 250), ('pages', 'Pages', 80), ('statut', 'Statut', 190))

    def __init__(self, root):
        self.root = root
        root.title(APP_NAME)
        self.cfg = self._load_cfg()
        root.geometry(self.cfg.get('geometry', '980x720'))
        root.minsize(820, 600)
        self.items = {}            # iid -> dict(path, kind, title, pagecount, pages, pdf)
        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.worker = None
        self.pending_scans = 0
        self._build()
        root.protocol('WM_DELETE_WINDOW', self._on_close)
        root.after(80, self._poll)

    # ---- configuration
    def _load_cfg(self):
        try:
            with open(CFG_PATH, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_cfg(self):
        self.cfg.update(out_dir=self.var_out.get(), pdf=self.var_pdf.get(), txt=self.var_txt.get(),
                        textlayer=self.var_layer.get(), raw=self.var_raw.get(),
                        open_when_done=self.var_open.get(), geometry=self.root.geometry())
        try:
            with open(CFG_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    # ---- interface
    def _build(self):
        root, cfg = self.root, self.cfg
        style = ttk.Style()
        for theme in ('vista', 'clam'):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        outer = ttk.Frame(root, padding=10)
        outer.pack(fill='both', expand=True)
        outer.rowconfigure(1, weight=3)
        outer.rowconfigure(5, weight=2)
        outer.columnconfigure(0, weight=1)

        # barre d'ajout
        bar = ttk.Frame(outer)
        bar.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        ttk.Button(bar, text='Ajouter des .xdf…', command=self.add_files).pack(side='left')
        ttk.Button(bar, text='Ajouter un dossier…', command=self.add_folder).pack(side='left', padx=6)
        ttk.Button(bar, text='Retirer', command=self.remove_selected).pack(side='left')
        ttk.Button(bar, text='Vider', command=self.clear).pack(side='left', padx=6)
        self.lbl_count = ttk.Label(bar, text='0 document')
        self.lbl_count.pack(side='right')

        # liste
        box = ttk.Frame(outer)
        box.grid(row=1, column=0, sticky='nsew')
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(box, columns=[c[0] for c in self.COLS], show='headings', selectmode='extended')
        for key, label, width in self.COLS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor='center' if key == 'pages' else 'w', stretch=key != 'pages')
        sb = ttk.Scrollbar(box, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        sb.grid(row=0, column=1, sticky='ns')
        self.tree.tag_configure('ok', foreground='#1a7f37')
        self.tree.tag_configure('err', foreground='#b3261e')
        self.tree.tag_configure('warn', foreground='#9a6700')
        self.tree.bind('<Double-1>', self._open_selected_pdf)
        self.tree.bind('<Delete>', lambda e: self.remove_selected())
        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label='Ouvrir le PDF', command=self._open_selected_pdf)
        self.menu.add_command(label='Ouvrir le dossier de sortie', command=self._open_out_dir)
        self.menu.add_separator()
        self.menu.add_command(label='Retirer de la liste', command=self.remove_selected)
        self.tree.bind('<Button-3>', self._popup)
        self.tree.bind('<Button-2>', self._popup)

        # sortie
        outf = ttk.LabelFrame(outer, text='Sortie', padding=8)
        outf.grid(row=2, column=0, sticky='ew', pady=(8, 0))
        outf.columnconfigure(1, weight=1)
        ttk.Label(outf, text='Dossier :').grid(row=0, column=0, sticky='w')
        self.var_out = tk.StringVar(value=cfg.get('out_dir', DEFAULT_OUT))
        ttk.Entry(outf, textvariable=self.var_out).grid(row=0, column=1, sticky='ew', padx=6)
        ttk.Button(outf, text='Parcourir…', command=self.pick_out).grid(row=0, column=2)
        ttk.Button(outf, text='Ouvrir', command=self._open_out_dir).grid(row=0, column=3, padx=(6, 0))

        # options
        opt = ttk.LabelFrame(outer, text='Options', padding=8)
        opt.grid(row=3, column=0, sticky='ew', pady=(8, 0))
        self.var_pdf = tk.BooleanVar(value=cfg.get('pdf', True))
        self.var_txt = tk.BooleanVar(value=cfg.get('txt', True))
        self.var_layer = tk.BooleanVar(value=cfg.get('textlayer', True))
        self.var_raw = tk.BooleanVar(value=cfg.get('raw', False))
        self.var_open = tk.BooleanVar(value=cfg.get('open_when_done', True))
        ttk.Checkbutton(opt, text='Générer le PDF', variable=self.var_pdf).grid(row=0, column=0, sticky='w')
        ttk.Checkbutton(opt, text='Générer le texte (.txt)', variable=self.var_txt).grid(row=0, column=1, sticky='w', padx=14)
        ttk.Checkbutton(opt, text='Texte sélectionnable / recherche dans le PDF', variable=self.var_layer).grid(row=0, column=2, sticky='w')
        ttk.Checkbutton(opt, text='Extraire aussi les fichiers bruts (.xdf : docSrc.xml, .ebt, .swf)',
                        variable=self.var_raw).grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))
        ttk.Checkbutton(opt, text='Ouvrir le dossier à la fin', variable=self.var_open).grid(row=1, column=2, sticky='w', pady=(4, 0))

        # actions + progression
        act = ttk.Frame(outer)
        act.grid(row=4, column=0, sticky='ew', pady=(10, 0))
        act.columnconfigure(2, weight=1)
        self.btn_go = ttk.Button(act, text='Convertir', command=self.start)
        self.btn_go.grid(row=0, column=0)
        self.btn_cancel = ttk.Button(act, text='Annuler', command=self.cancel_run, state='disabled')
        self.btn_cancel.grid(row=0, column=1, padx=6)
        self.progress = ttk.Progressbar(act, mode='determinate', maximum=1000)
        self.progress.grid(row=0, column=2, sticky='ew', padx=(8, 0))
        self.lbl_status = ttk.Label(outer, text='Ajoutez des fichiers .xdf ou un dossier de cache DocBox.')
        self.lbl_status.grid(row=6, column=0, sticky='w', pady=(4, 0))

        # journal
        logf = ttk.LabelFrame(outer, text='Journal', padding=4)
        logf.grid(row=5, column=0, sticky='nsew', pady=(10, 0))
        logf.rowconfigure(0, weight=1)
        logf.columnconfigure(0, weight=1)
        self.log = tk.Text(logf, height=8, wrap='word', state='disabled', font=('Consolas', 9)
                           if sys.platform.startswith('win') else ('Monospace', 9))
        lsb = ttk.Scrollbar(logf, orient='vertical', command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        self.log.grid(row=0, column=0, sticky='nsew')
        lsb.grid(row=0, column=1, sticky='ns')

        self._enable_dnd()

    def _enable_dnd(self):
        """Glisser-déposer (optionnel, nécessite le paquet tkinterdnd2)."""
        try:
            self.root.tk.call('package', 'require', 'tkdnd')
            self.tree.drop_target_register('DND_Files')
            self.tree.dnd_bind('<<Drop>>', lambda e: self._add(self.root.tk.splitlist(e.data)))
        except Exception:
            pass

    # ---- ajout / retrait
    def add_files(self):
        paths = filedialog.askopenfilenames(title='Choisir des fichiers .xdf',
                                            filetypes=[('Documents DocBox', '*.xdf'), ('Tous les fichiers', '*.*')])
        if paths:
            self._add(paths)

    def add_folder(self):
        path = filedialog.askdirectory(title='Dossier de cache DocBox, dossier de document ou dossier de .xdf')
        if path:
            self._add([path])

    def _add(self, paths):
        known = {os.path.normcase(it['path']) for it in self.items.values()}
        srcs = [s for s in collect_sources(paths) if os.path.normcase(s) not in known]
        if not srcs:
            self._set_status('Aucune source valide trouvée (attendu : .xdf ou dossier de document DocBox).')
            return
        self.pending_scans += 1
        threading.Thread(target=self._scan, args=(srcs,), daemon=True).start()

    def _scan(self, srcs):
        for s in srcs:
            kind = 'xdf' if s.lower().endswith('.xdf') else 'dir'
            try:
                info = P.inspect(s)
                self.q.put(('row', dict(path=s, kind=kind, **info)))
            except Exception as ex:
                self.q.put(('row', dict(path=s, kind=kind, title='', pagecount=None, pages=0, error=str(ex))))
        self.q.put(('scan_done',))

    def _insert_row(self, it):
        pages = it.get('pages', 0)
        pc = it.get('pagecount')
        ptxt = f'{pages}/{pc}' if pc else str(pages)
        if it.get('error'):
            status, tag = f'Illisible : {it["error"][:60]}', 'err'
        elif pages == 0:
            status, tag = 'Aucune page dans la source', 'warn'
        elif pc and pages < pc:
            status, tag = f'Prêt (pages partielles : {pages} sur {pc})', ''
        else:
            status, tag = 'Prêt', ''
        iid = self.tree.insert('', 'end', values=(it.get('title') or '(sans titre)', it['path'], ptxt, status),
                               tags=(tag,) if tag else ())
        self.items[iid] = dict(it, pdf=None)
        self._update_count()

    def remove_selected(self):
        if self._running():
            return
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.items.pop(iid, None)
        self._update_count()

    def clear(self):
        if self._running():
            return
        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        self._update_count()

    def _update_count(self):
        n = len(self.items)
        self.lbl_count.config(text=f'{n} document{"s" if n > 1 else ""}')

    def pick_out(self):
        d = filedialog.askdirectory(title='Dossier de sortie', initialdir=self.var_out.get() or None)
        if d:
            self.var_out.set(os.path.normpath(d))

    def _popup(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self.menu.tk_popup(event.x_root, event.y_root)

    def _open_selected_pdf(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        pdf = self.items[sel[0]].get('pdf')
        if pdf and os.path.exists(pdf):
            open_path(pdf)
        else:
            self._set_status('Ce document n’a pas encore été converti.')

    def _open_out_dir(self):
        d = self.var_out.get().strip()
        if d and os.path.isdir(d):
            open_path(d)
        else:
            self._set_status('Le dossier de sortie n’existe pas encore.')

    # ---- journal / statut
    def _log(self, text):
        self.log.config(state='normal')
        self.log.insert('end', text + '\n')
        self.log.see('end')
        self.log.config(state='disabled')

    def _set_status(self, text):
        self.lbl_status.config(text=text)

    def _set_row(self, iid, status, tag=''):
        if iid in self.items:
            self.tree.set(iid, 'statut', status)
            self.tree.item(iid, tags=(tag,) if tag else ())

    # ---- conversion
    def _running(self):
        return self.worker is not None and self.worker.is_alive()

    def start(self):
        if self._running():
            return
        if not (self.var_pdf.get() or self.var_txt.get() or self.var_raw.get()):
            messagebox.showwarning(APP_NAME, 'Cochez au moins une sortie : PDF, texte ou fichiers bruts.')
            return
        todo = [iid for iid, it in self.items.items() if it.get('pages', 0) > 0 and not it.get('error')]
        if not todo:
            messagebox.showinfo(APP_NAME, 'Aucun document convertible dans la liste.')
            return
        out_dir = self.var_out.get().strip()
        if not out_dir:
            messagebox.showwarning(APP_NAME, 'Choisissez un dossier de sortie.')
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as ex:
            messagebox.showerror(APP_NAME, f'Dossier de sortie inutilisable :\n{ex}')
            return
        opts = dict(out_dir=out_dir, pdf=self.var_pdf.get(), txt=self.var_txt.get(),
                    layer=self.var_layer.get(), raw=self.var_raw.get())
        self._save_cfg()
        for iid in todo:
            self._set_row(iid, 'En attente')
        self.cancel.clear()
        self.btn_go.config(state='disabled')
        self.btn_cancel.config(state='normal')
        self.progress['value'] = 0
        self.worker = threading.Thread(target=self._work, args=(todo, opts), daemon=True)
        self.worker.start()

    def cancel_run(self):
        if self._running():
            self.cancel.set()
            self._set_status('Annulation en cours…')

    def _out_base(self, it, out_dir):
        if it['kind'] == 'xdf':
            name = os.path.splitext(os.path.basename(it['path']))[0]
        else:
            did = os.path.basename(it['path'].rstrip('/\\'))
            name = f"{safe_name(it.get('title') or did)} [{did}]"
        return os.path.join(out_dir, safe_name(name, 150))

    def _work(self, todo, opts):
        n = len(todo)
        ok = fail = 0
        for i, iid in enumerate(todo):
            it = self.items[iid]
            if self.cancel.is_set():
                self.q.put(('row_status', iid, 'Annulé', 'warn'))
                continue
            base = self._out_base(it, opts['out_dir'])
            self.q.put(('row_status', iid, 'Conversion…', ''))
            self.q.put(('log', f'[{i + 1}/{n}] {it["path"]}'))

            def prog(done, total, msg, i=i, iid=iid):
                self.q.put(('prog', (i + (done / total if total else 0)) / n, iid, i + 1, n, done, total, msg))

            def lg(s):
                self.q.put(('log', s))
            try:
                if opts['raw'] and it['kind'] == 'xdf':
                    X.extract_xdf(it['path'], base + '_brut', log=lg)
                pdf = None
                if opts['pdf']:
                    res = P.convert(it['path'], base + '.pdf', progress=prog, cancel=self.cancel,
                                    text_layer=opts['layer'], write_txt=opts['txt'], log=lg)
                    pdf = res['pdf']
                    errs = len(res['errors'])
                    status = f'OK : {res["pages_done"]}' + (f'/{res["pagecount"]}' if res['pagecount'] else '') + ' pages'
                    if errs:
                        status += f' ({errs} erreur{"s" if errs > 1 else ""})'
                elif opts['txt']:
                    res = P.extract_text(it['path'], base + '.txt', progress=prog, cancel=self.cancel, log=lg)
                    status = f'Texte : {res["pages_done"]} pages'
                else:
                    status = 'Fichiers bruts extraits'
                ok += 1
                self.q.put(('row_done', iid, status, pdf, 'ok'))
            except P.Cancelled:
                self.q.put(('row_status', iid, 'Annulé', 'warn'))
                self.cancel.set()
            except Exception as ex:
                fail += 1
                self.q.put(('log', f'  ERREUR : {ex!r}'))
                self.q.put(('row_status', iid, f'Erreur : {str(ex)[:70]}', 'err'))
        self.q.put(('finished', ok, fail, opts['out_dir'], self.cancel.is_set()))

    # ---- boucle d'événements (thread principal)
    def _poll(self):
        try:
            while True:
                ev = self.q.get_nowait()
                kind = ev[0]
                if kind == 'row':
                    self._insert_row(ev[1])
                elif kind == 'scan_done':
                    self.pending_scans = max(0, self.pending_scans - 1)
                    if not self.pending_scans:
                        self._set_status(f'{len(self.items)} document(s) dans la liste.')
                elif kind == 'log':
                    self._log(ev[1])
                elif kind == 'row_status':
                    self._set_row(ev[1], ev[2], ev[3])
                elif kind == 'row_done':
                    _, iid, status, pdf, tag = ev
                    if iid in self.items:
                        self.items[iid]['pdf'] = pdf
                    self._set_row(iid, status, tag)
                elif kind == 'prog':
                    _, frac, iid, idx, n, done, total, msg = ev
                    self.progress['value'] = int(frac * 1000)
                    self._set_status(f'Document {idx}/{n} — {msg} ({done}/{total})')
                elif kind == 'finished':
                    _, ok, fail, out_dir, cancelled = ev
                    self.btn_go.config(state='normal')
                    self.btn_cancel.config(state='disabled')
                    self.progress['value'] = 0 if cancelled else 1000
                    msg = 'Annulé.' if cancelled else f'Terminé : {ok} réussi(s), {fail} en erreur.'
                    self._set_status(msg)
                    self._log(msg)
                    if not cancelled and ok and self.var_open.get():
                        open_path(out_dir)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _on_close(self):
        if self._running():
            if not messagebox.askyesno(APP_NAME, 'Une conversion est en cours. Annuler et quitter ?'):
                return
            self.cancel.set()
        self._save_cfg()
        self.root.destroy()


def main():
    try:  # netteté sous Windows (DPI)
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        from tkinterdnd2 import TkinterDnD  # glisser-déposer si disponible
        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    app = App(root)
    if len(sys.argv) > 1:   # fichiers/dossiers passés en argument (ou « Ouvrir avec »)
        app._add(sys.argv[1:])
    root.mainloop()


if __name__ == '__main__':
    main()
