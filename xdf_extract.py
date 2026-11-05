#!/usr/bin/env python3
"""Extracteur / décodeur de conteneurs DocBox *.xdf (magic 'xdf4').

Usage : python xdf_extract.py fichier.xdf [dossier_sortie]

Disposition du conteneur (little-endian), vérifiée sur 3 .xdf + le cache DocBox :
  0x00  "xdf4" | u32 nb_entrées | u32 version(=4) | zéros jusqu'à 0x39
  0x39  table, par entrée : u32 flags | u32 A | u32 B | u32 crc32 | u64 FILETIME | nom\\0
  puis 4 octets (u32, rôle non élucidé), puis les entrées contiguës :
        [1 octet méthode][données]            (taille totale = A)
        méthode 0x01 : LZMA « alone » sans taille (props 5D 00 00 00 01) -> docSrc.xml
        méthode 0xFF : données stockées = fichier .ebt identique à celui du cache DocBox
  puis un trailer (~938 o : 0x00 + chaîne type base64 à alphabet permuté + pied "2q0S").
CRC de la table : CRC32 du XML décompressé (docSrc.xml) ou du fichier .ebt complet.

Fichier .ebt : [8 o "YBD"+ver+0000 (h.ebt seulement)] + 32 o (hash/sel, non élucidé)
               + flux zlib (78 9C / 78 DA).  h.ebt décompressé = SWF « FWS » v9 (polices) ;
               pages décompressées = fragments SWF (formes/texte), non rendus ici.
"""
import datetime
import lzma
import os
import struct
import sys
import zlib

HDR = 0x39


def filetime(v):
    try:
        return (datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=v // 10)).isoformat()
    except OverflowError:
        return hex(v)


def parse(data):
    if data[:4] != b"xdf4":
        raise ValueError("magic 'xdf4' absent")
    count, version = struct.unpack_from("<II", data, 4)
    pos, entries = HDR, []
    for _ in range(count):
        flags, packed, size, crc, ft = struct.unpack_from("<IIIIQ", data, pos)
        end = data.index(b"\0", pos + 24)
        entries.append(dict(flags=flags, packed=packed, size=size, crc=crc, ft=ft,
                            name=data[pos + 24:end].decode("utf-8", "replace")))
        pos = end + 1
    gap = data[pos:pos + 4]
    pos += 4
    for e in entries:
        e["method"] = data[pos]
        e["blob"] = data[pos + 1:pos + e["packed"]]
        pos += e["packed"]
    return version, gap, entries, data[pos:]


def decode(e):
    """Retourne (octets, crc_ok)."""
    if e["method"] == 0x01:
        b = e["blob"]
        out = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(
            b[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + b[5:])
    elif e["method"] == 0xFF:
        out = e["blob"]
    else:
        raise ValueError(f"méthode inconnue {e['method']:#x} pour {e['name']}")
    return out, (zlib.crc32(out) & 0xFFFFFFFF) == e["crc"] if e["method"] == 0xFF else \
        (zlib.crc32(out) & 0xFFFFFFFF) == e["crc"]


def unwrap_ebt(blob):
    """Décompresse un fichier .ebt (cache DocBox ou entrée .xdf)."""
    off = 40 if blob[:3] == b"YBD" else 32
    return zlib.decompress(blob[off:])


def extract_xdf(src, out, log=print):
    """Extrait un .xdf dans le dossier `out` : docSrc.xml, .ebt d'origine, .swf/.bin décompressés."""
    version, gap, entries, trailer = parse(open(src, "rb").read())
    os.makedirs(out, exist_ok=True)
    log(f"{src}: version={version}, {len(entries)} entrée(s), u32={gap.hex()}, trailer={len(trailer)} o")
    for e in entries:
        data, ok = decode(e)
        rel = e["name"].replace("\\", os.sep)
        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst) or out, exist_ok=True)
        open(dst, "wb").write(data)
        extra = ""
        if rel.endswith(".ebt"):
            dec = unwrap_ebt(data)
            ext = ".swf" if dec[:3] == b"FWS" else ".bin"
            open(dst + ext, "wb").write(dec)
            extra = f" -> {len(dec)} o décompressés ({ext})"
        log(f"  {e['name']:<28} méthode={e['method']:#04x} {len(data):>7} o crc={'OK' if ok else 'KO'} "
            f"{filetime(e['ft'])}{extra}")
    open(os.path.join(out, "_trailer.bin"), "wb").write(trailer)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + "_xdf"
    extract_xdf(src, out)


if __name__ == "__main__":
    main()
