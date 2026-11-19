# Ressources locales (`static/vendor/`)

Tout ce dossier est servi par le logiciel lui-même : aucune ressource n'est chargée depuis Internet. Les fichiers ont été téléchargés une fois, puis commités.

| Dossier | Contenu | Version | Licence |
| --- | --- | --- | --- |
| `alpine/` | Alpine.js (`cdn.min.js` de `alpinejs`) | 3.17.5 | MIT |
| `manrope/` | police Manrope variable, sous-ensemble latin (`@fontsource-variable/manrope`) | 5.3.0 | SIL OFL 1.1 (`LICENSE.txt`) |
| `tabler/` | icônes Tabler, version contour (`@tabler/icons`) | 3.50.0 | MIT (`LICENSE.txt`) |

htmx n'est pas ici : il est fourni par `django-htmx` (balise `{% htmx_script %}`).

## Icônes

Seules les icônes utilisées sont gardées : un fichier par icône dans `tabler/icons/`, assemblées dans `tabler/sprite.svg` par `scripts/construire_icones.py` (décision D27). Pour en ajouter une, déposer son SVG dans `tabler/icons/` et relancer :

```text
uv run python scripts/construire_icones.py
```
