"""Dessine l'icône de MAx Engine, `static/max-engine.ico` : un carré bleu aux coins arrondis
(le bleu des actions de la charte) et un « M » blanc.

Elle sert aux raccourcis du bureau (« MAx Engine », « Mettre à jour MAx Engine ») et
d'icône aux pages, donc à la fenêtre d'Edge dans la barre des tâches. Pour la refaire :

    uv run python scripts/construire_icone.py

Script sans dépendance (bibliothèque standard) : chaque taille est une image PNG, dessinée
point par point avec un lissage des bords, puis rangée dans le fichier `.ico`.
"""

import math
import struct
import zlib
from pathlib import Path

FICHIER = Path(__file__).resolve().parent.parent / "static" / "max-engine.ico"
TAILLES = (16, 20, 24, 32, 40, 48, 64, 256)
BLEU = (37, 99, 235)  # #2563eb, `accent` de la charte
BLANC = (255, 255, 255)
RAYON = 0.2  # coins arrondis, en part du côté
EPAISSEUR = 0.075  # demi-épaisseur du trait du « M »
# Le « M » : un trait continu, en part du côté (0 en haut à gauche).
M = [(0.27, 0.74), (0.27, 0.28), (0.5, 0.56), (0.73, 0.28), (0.73, 0.74)]
ECHANTILLONS = 4  # 4 × 4 points par pixel pour lisser les bords


def dans_le_carre(x: float, y: float) -> bool:
    """Le point est-il dans le carré aux coins arrondis ?"""
    cx = min(max(x, RAYON), 1 - RAYON)
    cy = min(max(y, RAYON), 1 - RAYON)
    return (x - cx) ** 2 + (y - cy) ** 2 <= RAYON**2


def distance_segment(x, y, a, b) -> float:
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - ax - t * dx, y - ay - t * dy)


def dans_le_m(x: float, y: float) -> bool:
    return any(distance_segment(x, y, M[i], M[i + 1]) <= EPAISSEUR for i in range(len(M) - 1))


def pixels(taille: int) -> bytes:
    """Les lignes de l'image, en RGBA, chacune précédée de son octet de filtre PNG (0)."""
    lignes = bytearray()
    pas = 1 / (taille * ECHANTILLONS)
    for py in range(taille):
        lignes.append(0)
        for px in range(taille):
            fond = blanc = 0
            for sy in range(ECHANTILLONS):
                for sx in range(ECHANTILLONS):
                    x = (px * ECHANTILLONS + sx + 0.5) * pas
                    y = (py * ECHANTILLONS + sy + 0.5) * pas
                    if dans_le_carre(x, y):
                        fond += 1
                        blanc += dans_le_m(x, y)
            total = ECHANTILLONS * ECHANTILLONS
            if fond == 0:
                lignes += b"\x00\x00\x00\x00"
                continue
            part = blanc / fond
            couleur = [round(b * (1 - part) + w * part) for b, w in zip(BLEU, BLANC, strict=True)]
            lignes += bytes([*couleur, round(255 * fond / total)])
    return bytes(lignes)


def png(taille: int) -> bytes:
    def bloc(nature: bytes, donnees: bytes) -> bytes:
        return (
            struct.pack(">I", len(donnees))
            + nature
            + donnees
            + struct.pack(">I", zlib.crc32(nature + donnees))
        )

    entete = struct.pack(">IIBBBBB", taille, taille, 8, 6, 0, 0, 0)  # 8 bits, RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + bloc(b"IHDR", entete)
        + bloc(b"IDAT", zlib.compress(pixels(taille), 9))
        + bloc(b"IEND", b"")
    )


def ico(images: dict[int, bytes]) -> bytes:
    """Un fichier `.ico` qui contient chaque taille en PNG (Windows Vista et suivants)."""
    entete = struct.pack("<HHH", 0, 1, len(images))
    position = 6 + 16 * len(images)
    repertoire = b""
    contenu = b""
    for taille, image in images.items():
        cote = 0 if taille >= 256 else taille  # 0 veut dire 256
        repertoire += struct.pack("<BBBBHHII", cote, cote, 0, 0, 1, 32, len(image), position)
        contenu += image
        position += len(image)
    return entete + repertoire + contenu


def main() -> None:
    FICHIER.write_bytes(ico({taille: png(taille) for taille in TAILLES}))
    print(f"Icône écrite : {FICHIER} ({FICHIER.stat().st_size} octets)")


if __name__ == "__main__":
    main()
