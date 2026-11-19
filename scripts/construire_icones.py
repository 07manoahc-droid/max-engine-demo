"""Assemble le sprite d'icônes à partir des fichiers Tabler de `static/vendor/tabler/icons/`.

Chaque fichier `nom.svg` devient un `<symbol id="icon-nom">` du fichier
`static/vendor/tabler/sprite.svg`. Pour ajouter une icône : déposer son SVG (version contour,
https://tabler.io/icons) dans le dossier `icons/`, puis relancer ce script :

    uv run python scripts/construire_icones.py

Script sans dépendance (bibliothèque standard uniquement).
"""

import re
from pathlib import Path

DOSSIER = Path(__file__).resolve().parent.parent / "static" / "vendor" / "tabler"
CONTENU_SVG = re.compile(r"<svg\b[^>]*>(.*)</svg>", re.DOTALL)
FOND_TRANSPARENT = re.compile(r'<path\s+stroke="none"[^>]*/>')


def construire_symbole(fichier: Path) -> str:
    """Retourne le `<symbol>` d'une icône (sans le tracé de fond transparent de Tabler)."""
    source = fichier.read_text(encoding="utf-8")
    correspondance = CONTENU_SVG.search(source)
    if correspondance is None:
        raise ValueError(f"{fichier.name} n'est pas un fichier SVG valide.")
    contenu = FOND_TRANSPARENT.sub("", correspondance.group(1))
    contenu = "\n".join(ligne.strip() for ligne in contenu.strip().splitlines())
    return f'<symbol id="icon-{fichier.stem}" viewBox="0 0 24 24">\n{contenu}\n</symbol>'


def main() -> None:
    fichiers = sorted((DOSSIER / "icons").glob("*.svg"))
    symboles = "\n".join(construire_symbole(f) for f in fichiers)
    sprite = f'<svg xmlns="http://www.w3.org/2000/svg" style="display: none">\n{symboles}\n</svg>\n'
    (DOSSIER / "sprite.svg").write_text(sprite, encoding="utf-8")
    print(f"{len(fichiers)} icônes écrites dans {DOSSIER / 'sprite.svg'}")


if __name__ == "__main__":
    main()
