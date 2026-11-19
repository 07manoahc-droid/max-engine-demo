# Captures d'écran

Toutes les captures sont prises sur la base de démonstration, jamais sur de vraies données :

```bash
uv run python manage.py migrate
uv run python manage.py donnees_demo
uv run python manage.py runserver
```

Sur une base neuve, la démo donne toujours les mêmes numéros de véhicules et de dépenses. Ouvrir <http://127.0.0.1:8000/qui-es-tu/>, choisir **Mathis**, puis ouvrir chaque page ci-dessous. Fenêtre de 1280 × 800 environ, format PNG, nom de fichier exact.

| Fichier | Page à ouvrir | Ce qu'elle montre |
| --- | --- | --- |
| `01-accueil.png` | <http://127.0.0.1:8000/> | tuiles d'indicateurs, cartes des véhicules en cours avec leur photo et leur statut |
| `02-fiche-vendue.png` | <http://127.0.0.1:8000/vehicules/1/> (Yamaha YZ125, vendue) | vente avec reprise du scooter, tableau du partage de la marge, phrase « Pour ce véhicule seul » (D88) |
| `03-depense-frais-de-port.png` | <http://127.0.0.1:8000/depenses/2/> (commande DEMO-1001) | 164,80 € payés pour deux lignes, 10,40 € de frais de port répartis : 95,96 € et 68,84 € |
| `04-fiche-en-reparation.png` | <http://127.0.0.1:8000/vehicules/4/> (Husqvarna TC 85) | pièces à commander, dépense payée à deux avec frais de port, photos avant et pendant |
| `05-comptes.png` | <http://127.0.0.1:8000/comptes/> | positions des associés, solde « Mathis doit 382,57 € à Axel », règlement enregistré |
| `06-bilan-sombre.png` | <http://127.0.0.1:8000/bilan/>, en mode sombre (interrupteur de thème dans la barre latérale) | bilan par véhicule et par année, véhicule abandonné et dépenses du garage |

Avant de commiter les images, vérifier qu'aucune fenêtre ni onglet d'un autre logiciel n'apparaît (barre des tâches, notifications, autres onglets du navigateur).
