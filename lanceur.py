"""Ouvre MAx Engine : le raccourci « MAx Engine » du bureau lance ce fichier avec
`pythonw` (sans terminal). Tout le travail est dans `core/launcher.py` (D84).

    .venv\\Scripts\\pythonw.exe lanceur.py
    .venv\\Scripts\\python.exe lanceur.py --consultation --port 8001   (lecture seule)
"""

import sys
from pathlib import Path

# Le dossier du logiciel d'abord : `config` et `core` s'importent d'ici.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.launcher import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
