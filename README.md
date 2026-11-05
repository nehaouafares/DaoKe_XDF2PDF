it is WORK 100%
<img width="447" height="447" alt="image" src="https://github.com/user-attachments/assets/8277b6c7-a15a-40b0-a342-ed3aa866bfe8" />

# DocBox XDF → PDF

Convertit les documents DocBox (`.xdf`, ou dossiers du cache `AppData\Local\DocBox\<id>`) en **PDF vectoriel**
(+ texte brut). Le texte reste sélectionnable / recherchable dans le PDF.

## Utilisation
1. `pip install -r requirements.txt`
2. `python xdf_gui.py` (ou `lancer_gui.bat`)
3. *Ajouter des .xdf…* ou *Ajouter un dossier…* (dossier de document, racine du cache DocBox, ou dossier de .xdf),
   choisir le dossier de sortie, *Convertir*.

Ligne de commande : `python xdf2pdf.py entrée [sortie.pdf]` — extraction brute : `python xdf_extract.py fichier.xdf`.
Exécutable Windows autonome : `build_exe.bat` (PyInstaller) -> `dist\DocBoxXDF2PDF.exe`.

## Limites
* Seules les pages **présentes dans la source** sont converties (un .xdf n'embarque que les pages déjà chargées ;
  le cache `Local\DocBox` est en général plus complet : préférez-le).
* Dégradés SWF rendus en aplat ; pas d'animations/boutons (documents statiques uniquement).

## Format (résumé)
`.xdf` = conteneur `xdf4` (table + entrées : LZMA pour `docSrc.xml`, stockage brut pour les `.ebt`).
`.ebt` = [en-tête 32 o (+ `YBD`... pour h.ebt)] + zlib -> fragments SWF (formes, `DefineText2`, polices `DefineFont2/3`, bitmaps).
