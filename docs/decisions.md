# Journal des décisions — MAx Engine

> Version publique : les prénoms des associés sont fictifs, et les documents internes cités (cahier des charges, feuille de route, import de l'Excel, skills et sous-agents de Claude Code) ne sont pas publiés.

Chaque décision importante est notée une fois, avec son contexte et ce qui a été écarté. On ne modifie jamais une décision : si on change d'avis, on ajoute une nouvelle entrée qui la remplace, et on passe l'ancienne au statut « remplacée par Dxx ».

Modèle d'entrée :

```markdown
## Dxx — Titre court
- Date : AAAA-MM-JJ · Statut : adoptée | remplacée par Dyy
- Contexte : pourquoi la question se pose.
- Décision : ce qui est retenu.
- Alternatives écartées : et pourquoi.
- Conséquences : ce que cela implique pour le code.
```

## D01 — Logiciel local, pas de version en ligne
- Date : 2026-10-27 · Statut : adoptée
- Contexte : deux associés, toute la saisie se fait sur le portable Windows d'Axel.
- Décision : application installée et exécutée sur ce portable uniquement.
- Alternatives écartées : hébergement en ligne (coût mensuel, sécurité à gérer, inutile puisque la saisie se fait sur un seul poste).
- Conséquences : pas de comptes ni de mots de passe ; on choisit simplement « qui suis-je » au lancement. Rien n'est prévu pour un passage en ligne.

## D02 — Python et Django
- Date : 2026-10-27 · Statut : adoptée
- Contexte : développeur seul, maîtrise Python, Java et C, sans expérience web en solo ; il doit pouvoir relire le code produit par Claude Code.
- Décision : Python 3.13 + Django 5.2 LTS (maintenu jusqu'en avril 2028), environnement géré par `uv`.
- Alternatives écartées : Rust (non maîtrisé, atouts inutiles ici, code difficile à relire seul) ; Next.js (langage nouveau) ; PySide6 (rendu moins moderne) ; Django 6.0 (maintenu seulement jusqu'en avril 2027).
- Conséquences : passage à la LTS suivante (6.2) à prévoir en 2027-2028.

## D03 — Interface en HTMX, Tailwind et Alpine.js
- Date : 2026-10-27 · Statut : adoptée
- Contexte : interface moderne et réactive sans framework JavaScript à apprendre.
- Décision : gabarits Django + HTMX (`django-htmx`, `django-template-partials`), Tailwind CSS 4 via `django-tailwind-cli` (sans Node.js), Alpine.js pour les petites interactions.
- Alternatives écartées : React / Vue (complexité, deuxième langage).
- Conséquences : toutes les ressources (htmx, Alpine, police Manrope, icônes Tabler, Chart.js) sont servies localement depuis `static/` ; aucun CDN.

## D04 — Base SQLite stockée hors de OneDrive
- Date : 2026-10-27 · Statut : adoptée
- Contexte : volume faible (≤ 10 véhicules en cours), un seul poste.
- Décision : SQLite dans `donnees/garage.sqlite3`, sur le disque local du portable, jamais dans un dossier synchronisé.
- Alternatives écartées : PostgreSQL (serveur à installer) ; base dans OneDrive (risque de corruption lors de la synchronisation d'un fichier ouvert).
- Conséquences : sauvegardes par copie cohérente (API de sauvegarde de SQLite), voir D05.

## D05 — Sauvegardes vers OneDrive, consultation en lecture seule pour Mathis
- Date : 2026-10-27 · Statut : adoptée
- Contexte : ne jamais perdre de données ; Mathis veut suivre l'activité à distance.
- Décision : copie automatique de la base et des fichiers joints à chaque fermeture du logiciel, une fois par jour et avant chaque mise à jour, vers un dossier OneDrive partagé avec Mathis. Les 30 dernières copies sont conservées. Sur son ordinateur, Mathis ouvre la dernière copie en lecture seule.
- Alternatives écartées : synchroniser la base vivante ; version en ligne.
- Conséquences : commande `sauvegarder` et mode `LECTURE_SEULE` (tous les formulaires désactivés) à développer à l'étape 1.

## D06 — Code sur GitHub privé, jamais les données
- Date : 2026-10-27 · Statut : adoptée
- Contexte : mises à jour à distribuer sur le portable d'Axel.
- Décision : dépôt GitHub privé pour le code. `donnees/`, `media/`, `sauvegardes/` et `*.sqlite3` sont exclus de Git.
- Conséquences : raccourci « Mettre à jour » sur le portable : sauvegarde, `git pull`, `uv sync`, migrations, relance.

## D07 — Montants en décimaux exacts
- Date : 2026-10-27 · Statut : adoptée
- Décision : `Decimal` partout, `DecimalField(max_digits=10, decimal_places=2)`, arrondi `ROUND_HALF_UP` au centime. Jamais de `float`.

## D08 — Partage : chaque euro appartient pour moitié à chacun, solde calculé en continu
- Date : 2026-10-27 · Statut : adoptée
- Contexte : Mathis et Axel financent selon leurs moyens ; le but est de toujours gagner ou perdre la même somme. Le temps de travail ne compte pas.
- Décision : tout ce qui est dépensé ou encaissé pour le garage appartient pour moitié à chaque associé. Le solde entre associés est calculé en continu, comme le calcul « Égalisation » du fichier Excel ; les règlements se font en général à chaque vente.
- Alternatives écartées : partage au prorata des apports ; prise en compte des heures.
- Conséquences : formules dans la skill `regles-financieres` ; contrôle de non-régression sur les 205,44 € de l'Excel.

## D09 — Outillage : investissement du garage ou achat personnel
- Date : 2026-10-27 · Statut : adoptée
- Décision : à chaque dépense, une case « achat personnel ». Cochée, la dépense est gardée pour mémoire et exclue de tous les calculs ; sinon, elle compte pour moitié à chacun (D08).

## D10 — Une dépense = un paiement + des lignes
- Date : 2026-10-27 · Statut : adoptée
- Contexte : l'Excel montre des commandes groupées (plusieurs pièces, un total frais de port compris).
- Décision : `Expense` porte le montant réellement payé et les payeurs ; `ExpenseLine` porte chaque pièce (référence, quantité, prix, véhicule, statut). Les frais de port (écart entre total payé et somme des lignes) sont répartis sur les lignes au prorata de leur prix.
- Conséquences : l'achat d'un véhicule est aussi une ligne de dépense (catégorie système `vehicle_purchase`).

## D11 — Modèle de données complet dès le départ, écrans par étapes
- Date : 2026-10-27 · Statut : adoptée
- Décision : `docs/modele-de-donnees.md` décrit tout le schéma, y compris ce qui sert aux étapes 2 et 3 (transferts, annonces comparables, reprise). Les écrans sont construits en trois étapes, chacune utilisable.
- Conséquences : chaque évolution passe par une migration réversible, relue par le sous-agent `gardien-des-donnees`.

## D12 — Aucune API payante
- Date : 2026-10-27 · Statut : adoptée
- Décision : pas de veille automatique des annonces, pas de lecture automatique des factures. Les annonces comparables et les factures sont saisies à la main.
- Alternatives écartées : agent de veille (Leboncoin bloque les robots ; clé API payante) ; lecture des factures par l'API Claude (clé payante, données envoyées à un service externe).

## D13 — Jamais de suppression définitive
- Date : 2026-10-27 · Statut : adoptée
- Décision : suppression logique (`deleted_at`) et corbeille ; historique `django-simple-history` sur les données d'argent et de statut ; un règlement entre associés ne se modifie pas, il s'annule par un règlement inverse.

## D14 — Nom d'un véhicule
- Date : 2026-10-27 · Statut : adoptée
- Décision : `n°<numéro interne> · <marque> <modèle> <année> · <VIN>`, VIN omis s'il est inconnu. Le numéro est attribué automatiquement et n'est jamais réutilisé.

## D15 — Langue du code
- Date : 2026-10-27 · Statut : adoptée
- Décision : interface en français ; noms dans le code en anglais avec le glossaire de `CLAUDE.md` ; commentaires, documentation et messages de commit en français.
- Raison : Django et ses bibliothèques sont en anglais ; un code homogène est plus simple à maintenir.

## D16 — Charte graphique « vivante »
- Date : 2026-10-27 · Statut : adoptée
- Décision : style clair et moderne, police Manrope, bleu (#2563EB) réservé aux actions, une couleur par statut et par associé (Mathis indigo, Axel turquoise), coins légèrement arrondis, modes clair et sombre au choix. Détail dans la skill `charte-graphique`.

## D17 — Lancement en fenêtre d'application
- Date : 2026-10-27 · Statut : adoptée
- Contexte : Axel ne doit jamais voir de terminal.
- Décision : une icône sur le bureau démarre le serveur local `waitress` puis ouvre Microsoft Edge en mode application (`--app=http://127.0.0.1:8000`).
- Conséquences : script de lancement Windows à fournir à l'étape 1 ; le serveur s'arrête à la fermeture de la fenêtre.

## D18 — Statuts de pièce et pièces à commander
- Date : 2026-10-27 · Statut : adoptée
- Décision : statuts `TO_ORDER`, `ORDERED`, `RECEIVED`, `FITTED`, `RETURNED`. Une ligne « À commander » n'a pas encore de paiement et ne compte dans aucun calcul. Une liste regroupe les pièces à commander de tous les véhicules.

## D19 — Acompte et reprise
- Date : 2026-10-27 · Statut : adoptée
- Décision : un acompte fait passer le véhicule en « Réservé » ; il est déduit du prix de vente, ou rendu, ou conservé si la vente est annulée. Une reprise compte dans le prix de vente et crée le véhicule repris, acheté pour moitié par chaque associé (neutre pour le solde).

## D20 — Dossier de vente PDF en deux versions (étape 3)
- Date : 2026-10-27 · Statut : adoptée (outil PDF à choisir à l'étape 3)
- Décision : version acheteur (travaux et factures cochées, sans prix d'achat ni marge) et version interne (tous les montants). Les photos de tickets deviennent des pages PDF.
- À décider à l'étape 3 : bibliothèque de génération (WeasyPrint, xhtml2pdf ou impression par Edge) et `pypdf` pour la fusion des factures. Vérifier l'installation sous Windows avant de choisir.

## D21 — Une vente est une clé étrangère, une seule vente non annulée par véhicule
- Date : 2026-10-27 · Statut : adoptée
- Contexte : `Sale.vehicle` était en 1-1, ce qui interdisait de revendre un véhicule dont la première vente avait été annulée.
- Décision : `Sale.vehicle` est une FK, avec `UniqueConstraint(fields=["vehicle"], condition=Q(cancelled_at__isnull=True))`. `Sale` n'a pas de `deleted_at` ; si un champ de suppression logique y est ajouté, sa condition (`deleted_at__isnull=True`) rejoint celle de la contrainte.
- Alternatives écartées : garder le 1-1 et réutiliser la même vente après annulation (perd la trace de la vente annulée).
- Conséquences : le code qui cherche « la vente d'un véhicule » filtre sur `cancelled_at` vide.

## D22 — Dépenses verrouillées sur un véhicule vendu, « Rouvrir la fiche »
- Date : 2026-10-27 · Statut : adoptée
- Contexte : « vendu et réglé » n'avait pas de sens, car un règlement entre associés (`Settlement`) n'est lié à aucun véhicule.
- Décision : les dépenses d'un véhicule `SOLD` sont verrouillées. « Rouvrir la fiche » renseigne `Vehicle.reopened_at` et `Vehicle.reopen_reason` (motif obligatoire) et déverrouille ; « Verrouiller la fiche » remet `reopened_at` à vide. Le statut reste `SOLD`, le véhicule reste dans « Vendus » et dans le bilan. Les deux champs sont suivis par l'historique de `Vehicle`.
- Alternatives écartées : un `StatusChange` avec motif (faisait sortir le véhicule de `SOLD`) ; un statut « réglé ».
- Conséquences : marges, coûts et soldes se calculent toujours sur les ventes non annulées, jamais sur le statut ni sur `reopened_at`. Écrans en 1.5.

## D23 — Le modèle Contact est créé en 1.2
- Date : 2026-10-27 · Statut : adoptée
- Contexte : l'achat (1.3) et la vente (1.5) doivent pouvoir créer un vendeur ou un client sans changer d'écran, alors que les contacts étaient en 1.7.
- Décision : le modèle `Contact` est créé en 1.2 avec les autres. La création d'un contact depuis l'achat (1.3) et la vente (1.5) fait partie de ces tâches. Seul l'écran de liste des contacts reste en 1.7.

## D24 — Le modèle Partner est créé dès la tâche 1.1
- Date : 2026-10-27 · Statut : adoptée
- Contexte : le choix de l'utilisateur au lancement (1.1) exige les associés en base.
- Décision : `Partner` et sa migration de données (Mathis indigo, Axel turquoise) sont créés en 1.1, avec les champs définitifs de `docs/modele-de-donnees.md`. La tâche 1.2 ne les modifie pas.
- Alternatives écartées : une liste codée en dur, à remplacer ensuite.

## D25 — Le CSS Tailwind construit est commité
- Date : 2026-10-27 · Statut : adoptée
- Contexte : `django-tailwind-cli` télécharge un binaire Tailwind ; le portable d'Axel ne doit rien compiler.
- Décision : le CSS construit (`static/css/`) est commité. `tailwind build` est une étape obligatoire de la skill `publier-version`.
- Alternatives écartées : compiler sur le portable d'Axel à chaque mise à jour (binaire à télécharger, plus fragile).

## D26 — DEBUG actif par défaut seulement via manage.py
- Date : 2026-10-27 · Statut : adoptée
- Décision : `config/settings.py` a `DEBUG = False` par défaut (valeur sûre). `manage.py` définit `DEBUG=1` par défaut pour le développement. Le lanceur Waitress (1.7) n'utilise pas `manage.py` et reste donc en production.
- Conséquences : le service des fichiers statiques en production (WhiteNoise ou vue dédiée) reste à choisir en 1.7 (tranché par D85 : vue de Django).

## D27 — Sprite d'icônes Tabler réduit
- Date : 2026-10-27 · Statut : adoptée
- Décision : un petit sprite SVG ne contient que les icônes utilisées, assemblé par `scripts/construire_icones.py` à partir des fichiers Tabler (licence MIT, commitée à côté). Il s'agrandit quand une icône est ajoutée.
- Alternatives écartées : le sprite complet de Tabler (plusieurs Mo, milliers d'icônes inutiles).

## D28 — L'associé courant est conservé un an en session
- Date : 2026-10-27 · Statut : adoptée
- Décision : `SESSION_COOKIE_AGE = 365 * 24 * 3600`. Axel ne refait pas son choix à chaque lancement ; le lien « Changer » reste disponible dans la barre latérale.

## D29 — Choix de mise en œuvre du socle (1.1)
- Date : 2026-10-27 · Statut : adoptée
- Décision :
  - La feuille source Tailwind est `styles/input.css`, hors de `static/` (`django-tailwind-cli` le déconseille : `collectstatic` la collecterait). Le CSS construit est `static/css/tailwind.css`, version de Tailwind figée à 4.3.3 (`TAILWIND_CLI_VERSION`).
  - `tailwind runserver` réécrit `static/css/tailwind.css` en version non minifiée : lancer `tailwind build` avant tout commit qui touche aux gabarits ou à `styles/input.css`, et avant chaque publication (D25).
  - Le binaire Tailwind téléchargé (`.django_tailwind_cli/`, environ 110 Mo) est ignoré par Git.
  - La page « Qui es-tu ? » est à l'adresse `/qui-es-tu/` (tutoiement, comme l'interface).
  - La clé secrète de Django vient de la variable `SECRET_KEY`, sinon d'un fichier `donnees/secret.key` créé au premier lancement (hors Git). Elle doit rester stable car elle signe les sessions d'un an (D28).
  - Police Manrope : un seul fichier variable (sous-ensemble latin) couvre les graisses 400 et 600.

## D30 — L'auteur d'une modification dans l'historique est un associé
- Date : 2026-10-28 · Statut : adoptée
- Contexte : `django-simple-history` enregistre normalement un utilisateur Django ; le logiciel n'a pas de comptes (D01), seulement l'associé choisi au lancement.
- Décision : chaque historique est créé par `core.history.partner_history()`, soit `HistoricalRecords(user_model="core.Partner")`. L'auteur est lu dans `request.partner`, rendu visible par `simple_history.middleware.HistoryRequestMiddleware`. Sans requête (commande, migration), l'auteur est vide.
- Alternatives écartées : créer un utilisateur Django par associé (mots de passe inutiles, contraire à D01).
- Conséquences : le journal des modifications (1.7) affiche « Axel a modifié… ». Une modification faite depuis l'administration de secours est attribuée à l'associé choisi dans la session, s'il y en a un.

## D31 — Parts des associés dans une dépense payée à deux
- Date : 2026-10-28 · Statut : adoptée
- Contexte : arrondies ligne par ligne, les parts d'un associé sur une dépense peuvent différer d'un centime de ce qu'il a payé (10,00 € payés 5,00 € chacun sur 3 lignes : Mathis obtiendrait 5,01 €).
- Décision : le paiement de chaque associé, sauf le dernier, est réparti sur les lignes au prorata de leur total réparti, par la méthode du plus grand reste ; le dernier payeur reçoit le reste de chaque ligne. Les parts d'une ligne somment à son total réparti, et les parts d'un associé somment à son paiement.
- Alternatives écartées : l'arrondi ligne par ligne (écart d'un centime sur l'investissement affiché).
- Conséquences : `accounting.services.partner_shares`. Le solde n'est pas concerné (il part des paiements) ; seul l'investissement par véhicule l'est.

## D32 — Un acompte conservé compte dans la marge du véhicule
- Date : 2026-10-28 · Statut : adoptée
- Contexte : « un acompte `KEPT` est un encaissement du véhicule » ne disait pas comment il entre dans la marge.
- Décision : marge = prix de la vente non annulée + acomptes conservés − coût de revient. Tant que le véhicule n'a pas de vente non annulée, il n'a pas de marge (l'acompte conservé compte quand même dans la position de celui qui l'a reçu).
- Conséquences : `accounting.services.vehicle_margin`.

## D33 — Un écart d'un centime entre les positions est l'équilibre
- Date : 2026-10-28 · Statut : adoptée (choix de Mathis)
- Contexte : avec l'ancien cas 10 (positions 100,01 et 100,00 : Axel doit 0,01 €), régler ce centime faisait passer la dette dans l'autre sens, sans fin. Le cas 10 et l'invariant 4 (« après règlement, le solde vaut 0,00 € ») se contredisaient.
- Décision : si l'écart entre les deux positions vaut au plus 0,01 €, le solde est « à l'équilibre ». Sinon, le montant dû est la moitié de l'écart, arrondie au centime (`ROUND_HALF_UP`) en faveur de celui qui a avancé : avec un écart de 0,03 €, 0,02 € sont dus. Après règlement, il reste au plus un centime d'écart, donc l'équilibre.
- Alternatives écartées : garder l'ancien cas 10 et ne garantir l'invariant 4 que pour un écart pair.
- Conséquences : `accounting.services.balance` ; cas 10 de `exemples.md` et skill `regles-financieres` mis à jour.

## D34 — L'argent rendu après une vente annulée n'est pas une dépense
- Date : 2026-10-28 · Statut : adoptée (choix de Mathis)
- Contexte : la skill `regles-financieres` disait à la fois « les encaissements d'une vente annulée ne comptent plus » et « le remboursement à l'acheteur est enregistré comme une dépense `other` ». Les deux ensemble comptent deux fois le même argent : l'associé qui a encaissé puis rendu 2 400 € paraîtrait avoir avancé 2 400 €.
- Décision : les encaissements d'une vente annulée ne comptent plus, et l'argent rendu n'est pas enregistré comme une dépense. Ce que le garage garde passe par l'acompte conservé (`KEPT`).
- Alternatives écartées : garder les encaissements et enregistrer le remboursement en dépense (la dépense s'ajouterait au coût de revient du véhicule).
- Conséquences : aucun changement de code (`partner_positions` écarte déjà les ventes annulées) ; l'écran d'annulation (1.5) ne crée pas de dépense. Un acompte `APPLIED` resté sur une vente annulée est signalé par `controle_comptes`.

## D35 — Changements de statut permis à la main
- Date : 2026-10-28 · Statut : adoptée (choix de Mathis)
- Contexte : le modèle de données permettait tout retour en arrière sauf depuis `SOLD`. Revenir en « En attente d'achat » laisserait pourtant une dépense d'achat sur un véhicule réputé non acheté.
- Décision : le menu « Changer le statut » propose seulement :
  - pour un véhicule dont l'achat est enregistré : `PURCHASED`, `IN_REPAIR`, `READY_FOR_SALE`, `FOR_SALE` et `ABANDONED`, dans n'importe quel ordre, mais jamais `WAITING_PURCHASE` ;
  - pour un véhicule sans achat : `ABANDONED`, et le retour en `WAITING_PURCHASE` depuis `ABANDONED`. Le passage en `PURCHASED` se fait par le formulaire d'achat ;
  - rien pour `RESERVED` et `SOLD` : on en sort en annulant l'acompte ou la vente (écrans de la 1.5), et on n'y entre que par eux.
- Alternatives écartées : permettre le retour en « En attente d'achat » (dépense d'achat orpheline) ; le permettre en mettant la dépense d'achat à la corbeille (une action sur l'argent cachée derrière un changement de statut).
- Conséquences : `vehicles.services.allowed_statuses`. Pour annuler un achat saisi par erreur, on met sa dépense à la corbeille (1.7).

## D36 — Liste « En cours » dans la barre latérale
- Date : 2026-10-28 · Statut : adoptée (choix de Mathis)
- Contexte : la charte titrait « Au garage » la liste des véhicules de la barre latérale, alors que le menu a déjà une entrée « Au garage » juste au-dessus.
- Décision : la section s'appelle « En cours ». Elle liste les mêmes véhicules que l'écran « Véhicules au garage » (tous sauf Vendu et Abandonné). Elle est masquée quand la barre est repliée.
- Alternatives écartées : « Au garage » (doublon avec le menu) ; « Motos en cours » (le garage pourra avoir d'autres véhicules).
- Conséquences : skill `charte-graphique` mise à jour (barre latérale et barre repliée).

## D37 — Photos des véhicules
- Date : 2026-10-28 · Statut : adoptée (choix de Mathis pour le HEIC)
- Contexte : la galerie de la fiche (tâche 1.3) reçoit des photos de téléphone, glissées-déposées ou choisies.
- Décision :
  - formats acceptés : JPEG, PNG et WebP, reconnus par leurs premiers octets et non par leur nom seul ; 20 Mo au plus par photo ;
  - HEIC refusé, même renommé en .jpg, avec le conseil « Ce format n'est pas lu. Envoie la photo en JPEG (réglage « Le plus compatible » de l'iPhone). » ; Edge ne sait pas l'afficher ;
  - les fichiers refusés n'empêchent pas d'ajouter les autres du même envoi ;
  - photo de couverture d'une carte : la première photo de l'étape la plus avancée (Après, sinon Pendant, Avant, Annonce) ;
  - mise à la corbeille par suppression logique, le fichier reste dans `media/`.
- Alternatives écartées : convertir le HEIC sur place (dépendance lourde, Pillow et un greffon HEIF) ; Pillow pour vérifier les images (les premiers octets suffisent ici).
- Conséquences : `vehicles.services` (`photo_problem`, `add_photos`, `cover_photos`). Les vignettes affichaient les photos d'origine ; miniatures depuis la 2.0 : D92.

## D38 — « Marge des ventes » sur l'accueil, pas « Bénéfice »
- Date : 2026-10-28 · Statut : remplacée par D63 le 2026-11-03 (choix de Mathis)
- Contexte : le cahier des charges parle d'un bandeau avec « le bénéfice de l'année ».
- Décision : la tuile s'appelle « Marge des ventes <année> » : la somme des marges des véhicules dont la vente non annulée date de l'année. Les dépenses générales du garage n'y sont pas retirées.
- Alternatives écartées : l'appeler « Bénéfice » (faux dès qu'il y a de l'outillage) ; retirer les dépenses générales dès la 1.3 (le calcul du bénéfice appartient au bilan).
- Conséquences : `accounting.services.sales_margin_for_year`. Le vrai bénéfice, dépenses générales comprises, vient avec le bilan en 1.6.

## D39 — Retour en attente d'achat après la mise à la corbeille de l'achat
- Date : 2026-10-28 · Statut : adoptée (choix de Mathis) ; précise D35 sans la remplacer
- Contexte : D35 ne prévoit le retour en `WAITING_PURCHASE` que depuis `ABANDONED`. Or, pour annuler un achat saisi par erreur, D35 dit de mettre sa dépense à la corbeille : le véhicule reste alors en « Acheté » (ou plus loin) sans aucun achat enregistré.
- Décision : un véhicule sans achat enregistré (dépense d'achat à la corbeille) peut revenir en `WAITING_PURCHASE` depuis n'importe quel statut d'avant la vente, pas seulement depuis `ABANDONED`. On peut ensuite enregistrer l'achat correct.
- Alternatives écartées : obliger à passer par « Abandonné » avant de revenir en attente d'achat (une étape sans raison) ; remettre le statut automatiquement à la mise à la corbeille de la dépense (une action sur le statut cachée derrière la corbeille).
- Conséquences : `vehicles.services.allowed_statuses` (cas « sans achat ») ; `record_purchase` refuse un second achat si une dépense d'achat est restaurée entre-temps.

## D40 — Un paiement retiré d'une dépense part à la corbeille
- Date : 2026-10-29 · Statut : adoptée (choix de Mathis)
- Contexte : en modifiant une dépense (tâche 1.4), on peut retirer un payeur. `ExpensePayment` n'avait pas de `deleted_at` : le retirer l'aurait supprimé pour de bon, contre la règle 6.
- Décision : `ExpensePayment` hérite de `SoftDeleteModel`. Un payeur retiré est mis à la corbeille ; l'unicité (`expense`, `partner`) ne vaut que pour les paiements hors corbeille, si bien qu'un associé remis comme payeur reçoit un nouveau paiement. Les paiements à la corbeille ne comptent nulle part (soldes, coûts, `controle_comptes`) : le gestionnaire par défaut les écarte.
- Alternatives écartées : supprimer le paiement en ne gardant que sa trace dans l'historique (contraire à la règle 6) ; interdire de retirer un payeur (il faudrait refaire la dépense).
- Conséquences :
  - migration `expenses.0002_paiements_suppression_logique` ; `expenses.services.save_expense` ;
  - le retour arrière de la migration est refusé tant qu'un paiement est à la corbeille (sinon il redeviendrait actif et fausserait les comptes) ; revenir à une version précédente du code sur une base migrée aurait le même effet ;
  - la corbeille (1.7) ne restaure jamais un paiement seul, seulement la dépense entière (unicité et « somme des paiements = montant payé ») ;
  - une jointure ORM (`payments__amount`, `Sum(...)`) ne passe pas par le gestionnaire qui cache la corbeille : filtrer `payments__deleted_at__isnull=True` si on en écrit une.

## D41 — Un achat personnel ne concerne aucun véhicule
- Date : 2026-10-29 · Statut : adoptée (choix de Mathis)
- Contexte : un achat personnel est exclu de tous les calculs (D09). Rattaché à un véhicule, il ferait disparaître une pièce de son coût de revient sans que ça se voie.
- Décision : la case « Achat personnel » est refusée dès qu'une ligne de la dépense est rattachée à un véhicule. Message : « Un achat personnel ne peut pas concerner un véhicule. Décoche « Achat personnel » ou mets les lignes sur « Garage ». »
- Conséquences : `expenses.services.save_expense` et `expenses.forms.ExpenseForm.complete`.

## D42 — Saisie d'une dépense
- Date : 2026-10-29 · Statut : adoptée (plan de la tâche 1.4 validé par Mathis)
- Décision :
  - « Montant payé » laissé vide vaut la somme des lignes (pas de frais de port) : Axel ne tape pas deux fois le prix. En modification, il s'ouvre vide si la dépense n'avait pas de frais de port, pour suivre un prix corrigé.
  - Pendant la saisie, un aperçu (somme des lignes, frais de port, coût réparti de chaque ligne) est calculé par le serveur (`accounting.services.expense_preview`) et renvoyé par HTMX : aucun calcul d'argent dans le navigateur.
  - Fournisseurs proposés par une `<datalist>` de tous les noms ; la comparaison des noms se fait en Python avec `casefold()` (le `LOWER()` de SQLite ignore les lettres accentuées).
  - « Achat véhicule » n'est pas proposé dans une dépense : l'achat passe par son formulaire. La ligne d'achat d'une dépense modifiée garde sa catégorie et son véhicule, et ne peut pas être retirée ; son prix se corrige.
  - Une ligne déjà remboursée ne peut pas être retirée.
  - Corriger la date de la dépense d'achat corrige aussi la date d'achat du véhicule (`Vehicle.purchased_on`), qui ne se modifie nulle part ailleurs.
  - Changer le véhicule d'une ligne déjà enregistrée la déplace sans créer de `PartTransfer` : c'est une correction de saisie. Le vrai transfert de pièce (payeur conservé, trace gardée) a son écran depuis la 2.0 (D90).
  - Pas de nouvelle dépense pour un véhicule vendu (verrouillage complet en 1.5). Un véhicule abandonné reste proposé quand on vient de sa fiche (donneur de pièces).
  - Statut de pièce : seulement pour la catégorie « Pièces », « Commandé » par défaut, modifiable sur la ligne. « À commander » et « Retourné/Remboursé » ne se choisissent pas dans une dépense : ils viennent d'une pièce pas encore payée ou d'un retour.
  - Couleurs des statuts de pièce : tout en gris, sauf « À commander » en ambre ; le libellé est toujours écrit.
  - Factures : PDF, JPEG, PNG ou WebP, reconnus par leurs premiers octets, 20 Mo au plus, HEIC refusé (comme D37). Un fichier refusé bloque l'enregistrement de la dépense : on la corrige puis on rechoisit les factures. Une facture dont toutes les lignes visent le même véhicule est rangée dans `media/vehicules/<numéro>/`, sinon dans `media/garage/`.
  - Un bouton secondaire « Ajouter une dépense », sans véhicule, est sur l'accueil dès la 1.4 ; l'écran des dépenses générales reste en 1.6.
- Alternatives écartées : calcul des frais de port en JavaScript (règle 3) ; autocomplétion par une recherche HTMX (inutile pour quelques dizaines de fournisseurs).

## D43 — Fournisseurs comparés sans accents
- Date : 2026-10-29 · Statut : adoptée (choix de Mathis) ; précise D42 sans la remplacer
- Contexte : D42 compare les noms de fournisseurs avec `casefold()`, qui laisse les accents : « Electro Moto » et « Électro Moto » donnaient deux fournisseurs.
- Décision : la clé de comparaison (`expenses.services.supplier_key`) retire aussi les accents (décomposition Unicode NFKD, puis suppression des signes diacritiques), en plus des majuscules et des espaces en trop. Le nom enregistré garde ses accents tels que saisis la première fois.
- Alternatives écartées : fusionner les doublons déjà en base (inutile : seule la base d'essai en a).
- Conséquences : l'import Excel (1.7) passe par la même fonction.

## D44 — Pièces à commander
- Date : 2026-10-29 · Statut : adoptée (plan de la tâche 1.4 validé par Mathis)
- Décision :
  - Une pièce à commander est une ligne de dépense sans paiement (`expense` vide, statut « À commander »), catégorie « Pièces ». Elle ne compte dans aucun calcul tant que sa commande n'est pas enregistrée. Son prix est une estimation facultative (0,00 € si vide).
  - On la note depuis la fiche du véhicule ou depuis la page « Pièces à commander » (entrée de la barre latérale, avec le nombre de pièces en ambre). Le formulaire d'ajout prend plusieurs lignes (« Ajouter une ligne », « Retirer la ligne », une nouvelle ligne reprend le véhicule de la précédente) ; les lignes vides (sans désignation, référence ni prix) sont ignorées ; tout est enregistré ensemble ou rien. Une pièce se modifie et part à la corbeille, une à une, tant qu'elle n'est pas commandée.
  - Commander : on coche les pièces d'une même commande, puis « Enregistrer la commande » ouvre le formulaire de dépense avec ces pièces comme lignes (statut « Commandé », prix modifiable). À l'enregistrement, les lignes existantes sont rattachées à la dépense : elles gardent leur numéro, aucune copie. Une pièce déjà commandée entre-temps est refusée (« Recharge la page »).
  - Depuis la fiche du véhicule, « Enregistrer la commande » reprend toutes ses pièces à commander d'un coup ; on retire dans le formulaire celles qui ne font pas partie de la commande.
  - Une pièce commandée par erreur puis retirée de sa dépense part à la corbeille (elle ne redevient pas « À commander ») : on la restaure ou on la note à nouveau.
  - Sur la fiche du véhicule, chaque pièce payée propose l'étape suivante en un clic : « Marquer reçue » puis « Marquer montée ». Pour corriger (revenir de « Monté » à « Reçu »), le badge du statut ouvre un petit menu : Commandé, Reçu, Monté. Le choix reste aussi possible dans la modification de la dépense. « Retourné » ne vient que d'un retour (tâche suivante).
- Alternatives écartées : une table séparée pour la liste de courses (il faudrait recopier la pièce dans la dépense) ; un menu déroulant de statut sur chaque ligne de la fiche (plus de clics, plus d'erreurs pour Axel).

## D45 — Retours et remboursements
- Date : 2026-10-29 · Statut : adoptée (plan de la tâche 1.4 validé par Mathis)
- Décision :
  - « Enregistrer un retour » se fait depuis la fiche de la dépense, ligne par ligne (toute ligne payée, pas seulement les pièces). Le montant est prérempli avec ce qui reste à rembourser ; « Remboursé à » avec le payeur s'il est seul, sinon rien n'est choisi d'avance (« Choisis qui a reçu l'argent. »).
  - Plusieurs remboursements par ligne sont possibles ; leur somme ne dépasse pas le total réparti de la ligne (frais de port compris). Calculs dans `accounting.services` : `refund_left`, `refund_problem`, `is_fully_refunded`.
  - Une pièce passe en « Retourné » seulement quand elle est entièrement remboursée ; un remboursement partiel baisse son coût sans changer son statut.
  - Un remboursement de 0,00 € est accepté et affiché « montant à préciser » (retour pas encore remboursé ; reprise de l'Excel). Il ne change ni le coût ni le solde. « Préciser le montant » corrige le même remboursement, avec le reste à rembourser proposé.
  - Un remboursement « à préciser » ne fait jamais passer une pièce en « Retourné », mais ne l'en fait pas sortir non plus : une ligne de l'Excel marquée « Retourné/Remboursé » reprise avec un remboursement de 0,00 € (`docs/import-excel.md`) reste « Retourné » (`accounting.services.is_returned`).
  - Un remboursement se corrige ou part à la corbeille. Si la pièce n'est plus entièrement remboursée (corbeille, montant baissé, prix de la ligne corrigé) et n'a pas de remboursement « à préciser », elle redevient « Reçu » : le statut d'avant le retour n'est pas gardé, et une pièce retournée avait été reçue.
  - Une dépense qui a un remboursement ne devient pas un achat personnel (mettre d'abord le remboursement à la corbeille). Un remboursement déjà versé à un ancien associé se corrige sans changer de bénéficiaire.
  - Pas de retour sur un achat personnel (hors calculs) ni sur une pièce pas encore payée.
- Alternatives écartées : passer la pièce en « Retourné » dès le premier remboursement (un remboursement partiel la ferait disparaître des pièces à suivre) ; garder le statut d'avant le retour (un champ de plus pour un cas rare).

## D46 — Finitions de la tâche 1.4
- Date : 2026-10-30 · Statut : adoptée (choix de Mathis après l'essai ; D45 validée)
- Décision :
  - Dans le formulaire d'une dépense, le statut d'une ligne « Retourné » est affiché en lecture seule (badge, « Ce statut suit le remboursement. ») : il ne se change que par ses remboursements. Le service le garde aussi, quel que soit le statut reçu. La catégorie de cette ligne est verrouillée de la même façon : quitter « Pièces » puis y revenir effacerait le statut. Pour corriger la catégorie, on met d'abord le remboursement à la corbeille.
  - `controle_comptes` affiche un avertissement pour chaque retour à 0,00 € « à préciser » encore en attente. Ce n'est pas une incohérence : le code de sortie ne change pas, et les comptes restent « cohérents ».
  - Fournisseurs : le `<datalist>` de D42 est gardé, même si sa liste reste blanche en mode sombre. Elle est sans doute dessinée par Edge, qui ignorerait le style de la page. Une liste refaite avec Alpine.js coûterait plus qu'elle ne rapporterait.
- Alternatives écartées : compter un retour « à préciser » comme une erreur de `controle_comptes` (l'import Excel en crée volontairement, la commande échouerait).

## D47 — Acompte et statut « Réservé »
- Date : 2026-10-30 · Statut : adoptée (plan de la tâche 1.5 validé par Mathis) ; modifiée par D89 (versements)
- Contexte : le modèle ramenait toujours le véhicule en « En vente » quand un acompte est annulé, et ne disait ni depuis quels statuts prendre un acompte, ni combien un véhicule peut en avoir.
- Décision :
  - Un acompte se prend depuis « Acheté », « En réparation », « Prêt à vendre » ou « En vente », si l'achat est enregistré. Le véhicule passe en « Réservé ».
  - Un seul acompte en cours par véhicule (contrainte en base). S'il est complété, on corrige son montant.
  - Tant qu'il est en cours, un acompte se corrige (montant, date, qui l'a reçu, acheteur) ; l'ancienne valeur reste dans son historique. Un ancien associé reste possible sur un acompte qu'il avait déjà reçu.
  - Annuler un acompte demande s'il a été rendu ou conservé, sans choix d'avance, même sans vente : D19 ne parlait que d'une vente annulée, un acompte peut aussi être conservé quand la réservation tombe. Le véhicule revient au statut d'avant la réservation, lu dans `StatusChange` ; « En vente » si ce statut n'est pas retrouvé.
  - Un acompte rendu ou conservé reste visible sur la fiche.
  - `controle_comptes` signale un véhicule réservé sans un acompte en cours, ou un acompte en cours sur un véhicule qui n'est pas réservé.
- Alternatives écartées : toujours revenir en « En vente » (faux pour un véhicule encore en réparation) ; garder le statut d'avant dans un nouveau champ (`StatusChange` le connaît déjà) ; plusieurs acomptes en cours (limite notée à l'étape 2 de la feuille de route).
- Conséquences : `sales.services` (`deposit_refusal`, `record_deposit`, `update_deposit`, `cancel_deposit`, `status_before_reservation`) ; migration `sales.0002_un_seul_acompte_en_cours`. Depuis D89, un acompte se reçoit en plusieurs versements, par l'un ou l'autre associé : le montant ne se corrige plus sur l'acompte mais sur chaque versement, et un complément s'ajoute par « Ajouter un versement ».

## D48 — Transactions SQLite en mode IMMEDIATE
- Date : 2026-10-30 · Statut : adoptée (correction demandée par Mathis après relecture)
- Contexte : par défaut, une transaction SQLite ne réserve l'écriture qu'à sa première écriture. Deux envois simultanés qui lisent avant d'écrire (acompte, correction d'un acompte) finissaient en page d'erreur : « database is locked », aussitôt, sans attente. Reproduit à chaque essai avec deux onglets ; aucune donnée abîmée, la transaction perdante étant annulée.
- Décision : `DATABASES["default"]["OPTIONS"]["transaction_mode"] = "IMMEDIATE"`. Toute transaction `atomic` réserve l'écriture dès son début ; le second envoi attend le premier (5 secondes au plus), puis relit la base et reçoit un message normal.
- Alternatives écartées : attraper `OperationalError` dans chaque vue (à répéter partout, et on perdrait le refus clair) ; compter sur le bouton désactivé après le premier clic (ne protège pas de deux onglets).
- Conséquences : vaut pour tout le projet, y compris les migrations. Ne pas garder une transaction ouverte pendant une opération longue. Le mode lecture seule (1.7) ne doit ouvrir aucune transaction `atomic` : `BEGIN IMMEDIATE` échoue sur un fichier en lecture seule.

## D49 — Une vente se modifie tant qu'elle n'est pas annulée
- Date : 2026-10-30 · Statut : adoptée (plan de la tâche 1.5 validé par Mathis) ; ne change pas D32
- Contexte : le plan prévoyait d'annuler puis de ressaisir une vente pour corriger une erreur. C'est lourd pour une faute de frappe, et l'annulation change le statut du véhicule et demande le sort de l'acompte.
- Décision :
  - Une vente non annulée se modifie : prix, date, acheteur, lieu, notes, encaissements. Les anciennes valeurs restent dans les historiques de `Sale` et `SaleReceipt`.
  - La reprise ne se modifie pas : on annule la vente.
  - `SaleReceipt` hérite de `SoftDeleteModel`. Un encaissement retiré part à la corbeille et ne compte nulle part, sur le modèle de D40. Un associé remis sur la vente reçoit un nouvel encaissement.
  - Les encaissements suivent la date de la vente.
  - Reste à encaisser = prix − acompte déduit − reprise. Il peut valoir 0 € : personne n'a alors à encaisser. Un prix plus bas que ce qui est déjà reçu est refusé.
  - Une vente se fait depuis « Acheté », « En réparation », « Prêt à vendre », « En vente » ou « Réservé », si l'achat est enregistré. L'acompte en cours devient `APPLIED`.
  - Annuler une vente : le véhicule revient en « En vente », la fiche est verrouillée de nouveau (`reopened_at` vide), l'acompte déduit devient rendu ou conservé, et aucune dépense n'est créée (D34). La vente annulée reste visible sur la fiche.
  - La fiche affiche « Solde actuel entre vous », qui est le solde de tout le garage, et précise le montant du véhicule seul quand il diffère.
  - `controle_comptes` signale un véhicule vendu sans vente non annulée, et une vente non annulée sur un véhicule qui n'est pas vendu.
- Alternatives écartées : annuler et ressaisir (voir le contexte) ; une unicité (`sale`, `partner`) sur les encaissements, comme pour les paiements (elle interdirait d'encaisser en deux fois, alors que chaque encaissement a sa date) ; afficher seulement le montant du véhicule (il ne serait pas la somme à régler s'il reste d'autres dépenses au garage).
- Conséquences :
  - migration `sales.0003_encaissements_suppression_logique` ; le retour arrière est refusé tant qu'un encaissement est à la corbeille (même raison que D40) ;
  - `sales.services` (`sale_refusal`, `record_sale`, `update_sale`, `cancel_sale`) ; `accounting.services` (`sale_left_to_receive`, `sale_preview`, `sale_recap`) ;
  - la corbeille (1.7) ne restaure jamais un encaissement seul : la somme ne ferait plus le prix ;
  - si un associé a deux encaissements sur une vente, la modification n'en garde qu'un, car le formulaire donne un montant par associé ;
  - un ancien associé qui a encaissé seul reste proposé à la modification. S'il a encaissé avec un autre associé, le formulaire refuse la modification et dit de prévenir Mathis : « Montants différents » ne le propose pas, et son encaissement serait transféré sans que personne le voie.

## D50 — Verrou des dépenses d'un véhicule vendu
- Date : 2026-11-01 · Statut : adoptée (plan de la tâche 1.5 validé par Mathis) ; précise D22
- Contexte : D22 fixe le principe, sans dire ce que couvre le verrou, ni ce que deviennent les pièces à commander d'un véhicule vendu. Une fiche rouverte devait aussi pouvoir recevoir des dépenses, alors que l'écran testait encore le statut « Vendu ».
- Décision :
  - Le verrou s'applique dans les services, pas seulement à l'écran : ajout et modification d'une dépense (factures comprises), statut d'une pièce, retours, retrait d'une facture, pièces à commander. Les services relisent la base : un onglet resté ouvert est refusé avec un message.
  - Le contrôle de `save_expense` porte sur les véhicules avant et après modification : on ne sort pas une ligne d'un véhicule verrouillé, et on ne lui en ajoute pas.
  - Une dépense sur plusieurs véhicules est verrouillée dès qu'un de ses véhicules l'est, y compris sur la fiche rouverte de l'autre véhicule : la page de la dépense donne le lien vers chaque fiche à rouvrir.
  - Restent ouverts sur un véhicule vendu : « Modifier la fiche », les photos, et la vente tant qu'elle n'est pas annulée (D49). L'administration de secours (`/admin/`) passe volontairement outre le verrou : elle sert quand aucun écran ne permet de corriger.
  - Toute dépense propose les véhicules du garage et les véhicules vendus à la fiche rouverte, même sans ligne dessus : rouvrir sert à corriger ce qui est payé, y compris une facture saisie sur le garage par erreur. Un véhicule vendu non rouvert n'est jamais proposé (sauf s'il porte déjà une ligne, et la dépense est alors verrouillée).
  - Une dépense propose aussi les véhicules de ses propres lignes et celui de la fiche d'où l'on vient, et l'aperçu reçoit ce dernier. Cela couvre le véhicule abandonné, qui n'est pas proposé ailleurs. Limite acceptée : une ligne enregistrée hors d'un véhicule abandonné n'y revient qu'après un changement de statut du véhicule.
  - Un véhicule qui a quitté la liste pendant que la page restait ouverte est refusé à l'envoi, avec un message par cause. Il reste choisi à l'écran : une ligne ne part jamais sur le garage sans que quelqu'un l'ait choisi.
  - Une seule pièce cochée écartée (à la corbeille, déjà commandée, véhicule vendu ou abandonné, disparue) arrête toute la commande : le formulaire ne s'ouvre pas avec les autres, car l'écart de la facture passerait en frais de port. Le message nomme chaque pièce, donne la cause et quoi faire, et dit que rien n'est enregistré.
  - Un message d'erreur reste à l'écran jusqu'à ce qu'on le ferme ; seules les confirmations s'effacent après 4 secondes.
  - Pièces à commander : celles d'un véhicule vendu ou abandonné restent en base, sortent de la liste et du compteur, et reviennent si le véhicule revient au garage. On n'en ajoute plus sur ces véhicules, même sur une fiche rouverte : rouvrir sert à corriger ce qui est payé, pas à préparer de nouveaux achats. Le bloc « À commander » disparaît de la fiche.
  - Un refus dit toujours quoi faire (« Rouvre sa fiche pour les corriger. », « Change d'abord son statut. »).
  - Le bandeau de la fiche porte « Rouvrir la fiche » et sa phrase d'explication (« Tu pourras corriger les dépenses. N'oublie pas de verrouiller la fiche ensuite. »), puis « Verrouiller la fiche », seul bouton bleu d'une fiche rouverte.
  - Verrouiller garde le dernier motif dans `reopen_reason` ; seul l'historique de `Vehicle` conserve les motifs précédents. Le motif n'est affiché que sur une fiche rouverte.
- Alternatives écartées : verrouiller seulement à l'écran (un second onglet passerait) ; autoriser les pièces à commander sur une fiche rouverte (la liste se base sur le statut : elles y seraient invisibles) ; verrouiller ligne par ligne une dépense partagée (le paiement et les frais de port sont communs à toutes ses lignes).
- Conséquences : `expenses.services` (`locked_vehicles`, `locked_expense_ids`, `expense_lock_refusal`, `lock_message`, `line_vehicle_refusal`, `noted_parts`, `part_to_order_refusal`, `order_refusal`) ; `vehicles.services` (`reopen_vehicle`, `lock_vehicle`, `vehicles_open_to_expenses`). Aucune migration : les champs existaient depuis la 1.2. `parts_to_order()` ne renvoie plus tout : pour retrouver une pièce sortie de la liste, utiliser `noted_parts()`.

## D51 — Reprise d'un véhicule par l'acheteur
- Date : 2026-11-01 · Statut : adoptée (plan de la tâche 1.5 validé par Mathis) ; précise D19 et D49
- Contexte : D19 fixe le principe (la reprise compte dans le prix et crée un véhicule acheté pour moitié par chacun), sans dire comment la saisir, ni ce qu'elle devient à l'annulation de la vente, ni comment garder sa neutralité pour le solde dans le temps.
- Décision :
  - Saisie dans « Enregistrer la vente » : une case ouvre la valeur, le type, la marque, le modèle et l'année. La reprise se saisit à l'enregistrement, jamais à la modification : pour la corriger, on annule la vente (D49). À la modification, elle s'affiche en lecture seule.
  - `record_sale` enregistre tout ou rien : l'acheteur, le véhicule repris (`create_vehicle`), son achat (`record_purchase`), puis la vente. Le véhicule arrive en « Acheté », avec l'acheteur comme vendeur et la date de la vente comme date d'achat.
  - Le partage passe par `split_equally` dans l'ordre de `active_partners`, comme dans les positions : la reprise reste neutre au centime près, même sur une valeur impaire.
  - Une reprise est refusée s'il n'y a pas exactement deux associés actifs, et 0,01 € est refusé avec un message clair : une des deux parts vaudrait 0 € (`accounting.services.trade_in_problem`).
  - Tant que la vente n'est pas annulée, l'achat du véhicule repris garde son montant, ses payeurs et sa ligne unique : sinon la reprise ne serait plus neutre. La date, le fournisseur, le numéro de commande, la désignation, la référence, les notes et les factures restent modifiables (le numéro de commande n'est pas contrôlé) ; un retour sur la ligne d'achat reste possible. Précisé le 2026-11-08.
  - Modifier la date ou l'acheteur d'une vente ne change pas l'achat repris : la date se corrige dans la dépense, le vendeur dans « Modifier la fiche ».
  - Annuler une vente avec reprise met à la corbeille la dépense d'achat et la fiche du véhicule repris ; ses photos, interventions et tâches restent attachées à la fiche, cachées avec elle (D76, corrigé le 2026-11-08). L'écran de confirmation l'annonce. `Sale.trade_in_vehicle` garde le lien : la vente annulée affiche sa reprise, sans lien vers une fiche qui n'est plus visible.
  - L'annulation est refusée dès l'ouverture de l'écran, et de nouveau dans le service, si le véhicule repris porte autre chose que son achat : un acompte ou une vente, même terminés ou annulés (un acompte conservé compte dans l'argent) ; une autre dépense ; une autre ligne sur la dépense d'achat ; un remboursement ; une pièce à commander.
  - Le message dit quoi faire quand un écran le permet : retirer une pièce à commander, mettre un remboursement à la corbeille, déplacer les lignes des autres dépenses sur un autre véhicule ou sur le garage dans « Modifier la dépense » (en rouvrant d'abord la fiche d'un véhicule vendu qui partage la dépense). Il renvoie à Mathis sinon (acompte ou vente sur le véhicule repris, achat modifié) : jusqu'à la 1.7, seule l'administration de secours permet de sortir de ces derniers refus. Précisé le 2026-11-03.
  - Le solde « pour ce véhicule seul » (`sale_recap`) ne compte pas la reprise : l'achat du véhicule repris, porté par l'autre véhicule, l'annule exactement. La compter faisait apparaître un centime d'écart sur une valeur impaire. La reprise reste affichée dans « Déjà encaissé ». Corrigé le 2026-11-03.
  - `controle_comptes` vérifie que chaque reprise d'une vente non annulée reste neutre : un seul achat du véhicule repris, hors corbeille et non personnel, du montant de la reprise, payé comme `split_equally` la partage. Sinon, il le signale comme une incohérence (`accounting.services.trade_in_problems`). Ajouté le 2026-11-03.
- Alternatives écartées : recalculer l'achat repris à chaque modification de la vente (la reprise ne se modifie pas) ; laisser le véhicule repris au garage après l'annulation (son achat n'aurait plus de contrepartie, et le solde bougerait) ; déplacer d'office les autres dépenses sur le garage à l'annulation (décision d'argent à prendre par un associé, pas par l'écran).
- Conséquences : `accounting.services` (`trade_in_problem`, `trade_in_problems`, `SaleInput.trade_in_vehicle`) ; `accounting.queries.trade_in_purchases` ; `sales.services` (`TradeIn`, `trade_in_cancel_refusal`) ; `expenses.services.trade_in_sale` ; `vehicles.forms` (`TradeInVehicleForm`, `trade_in_vehicle_form`). Aucune migration : les champs de `Sale` existaient depuis la 1.2. Le véhicule repris et son achat restent à la corbeille sans lien d'annulation : la 1.7 ne doit jamais les restaurer seuls.

## D52 — Comptes en deux onglets
- Date : 2026-11-03 · Statut : adoptée (plan de la tâche 1.6 validé par Mathis)
- Contexte : le cahier des charges range sous « Comptes » le solde, l'historique des règlements et les dépenses générales du garage. Chacun a son action principale.
- Décision : « Comptes » a deux pages, « Solde et règlements » (`/comptes/`, bouton bleu « Enregistrer le règlement ») et « Dépenses du garage » (`/comptes/depenses-du-garage/`, bouton bleu « Ajouter une dépense du garage »), reliées par des onglets. Le lien « Comptes » de la barre latérale est allumé sur toutes les pages des comptes.
- Alternatives écartées : une seule page (deux boutons bleus, contraire à la charte).
- Conséquences : `accounting/views.py`, `templates/accounting/`. La page « Solde et règlements » montre aussi le détail du calcul (`accounting.services.position_details`). Les onglets arrivent avec la seconde page.

## D53 — Sens et montant d'un règlement
- Date : 2026-11-03 · Statut : adoptée (plan de la tâche 1.6 validé par Mathis)
- Contexte : un règlement saisi dans le mauvais sens, ou en double depuis deux onglets, ferait passer la dette dans l'autre sens sans que personne le voie.
- Décision :
  - Seul le sens dû est proposé : celui qui doit verse à l'autre. Le montant est prérempli avec ce qui est dû ; un règlement partiel est permis, un montant plus grand est refusé. À l'équilibre, il n'y a pas de bouton.
  - Le sens et le montant dû voyagent dans le formulaire (champs cachés). Le service relit le solde dans la transaction (D48) : s'il a changé entre l'ouverture de la page et l'envoi, le règlement est refusé (« Recharge la page »). Cela arrête aussi un second envoi depuis un autre onglet, même pour un règlement partiel (deux fois 100 € sur 205,44 € dus : le second est refusé). Correction après la relecture du 2026-11-03.
  - Un aperçu calculé par le serveur dit ce qu'il restera après le règlement.
  - La date d'un règlement ne peut pas être après aujourd'hui : on l'enregistre une fois l'argent versé. Une date passée reste permise (un versement de la veille se saisit le lendemain). Le message le dit : « Un règlement ne peut pas être daté après aujourd'hui : on l'enregistre une fois l'argent versé. Indique le jour du versement, aujourd'hui ou avant. » Le calendrier du navigateur grise les jours à venir. Choix de Mathis après l'essai du 2026-11-03.
  - Le bouton qui quitte le formulaire sans enregistrer s'appelle « Retour », comme sur le formulaire de vente : « Annuler » est réservé à l'annulation d'un règlement. Choix de Mathis après l'essai du 2026-11-03.
  - Un règlement compte en entier, sans partage : c'est de l'argent passé d'un associé à l'autre, pas de l'argent du garage. Il ne change ni la somme des positions, ni les coûts, ni les marges.
- Alternatives écartées : laisser choisir le sens et le montant (fautes de frappe, double envoi).
- Conséquences : `accounting.services` (`settlement_problem`, `balance_after`) ; `accounting.settlements` (`record_settlement` avec `seen_due`, `date_problem`). Un règlement fait d'avance (avant que la dépense ou la vente qui le justifie soit saisie) ne s'enregistre pas : on saisit d'abord la dépense ou la vente.

## D54 — Annulation d'un règlement
- Date : 2026-11-03 · Statut : adoptée (plan de la tâche 1.6 validé par Mathis) ; précise D13
- Décision : « Annuler » ouvre un écran de confirmation qui demande un motif, obligatoire. Le règlement inverse (`cancels`) est daté du jour, reprend le même montant dans l'autre sens et garde le motif dans ses notes. Les deux restent dans l'historique : l'annulé porte « Annulé le … », l'inverse « Annulation du règlement du … » et son motif. Une annulation ne s'annule pas : pour corriger, on enregistre un nouveau règlement. L'annulation n'est pas limitée par le montant dû. Un règlement qui concerne un ancien associé ne s'annule pas depuis l'écran (« Préviens Mathis »).
- Conséquences : `accounting.settlements` (`cancel_refusal`, `cancel_settlement`). Le règlement est relu dans la transaction : un double-clic n'annule qu'une fois.

## D55 — Raccourci « Enregistrer le règlement » depuis la vente
- Date : 2026-11-03 · Statut : adoptée (plan de la tâche 1.6 validé par Mathis)
- Contexte : les règlements se font en général à chaque vente (D08), et la fiche d'un véhicule vendu affiche déjà « Solde actuel entre vous » (D49).
- Décision : un bouton secondaire « Enregistrer le règlement » sous ce solde, absent à l'équilibre et en lecture seule. Il ouvre le formulaire (`?vehicule=<numéro>`), qui ramène à la fiche après l'enregistrement. Le montant proposé est le solde de tout le garage, pas celui du véhicule seul.
- Conséquences : `templates/components/bloc_vente.html`, `vehicles/views.py` (`settled`), `accounting.views.settlement_create`.

## D56 — Les écritures des comptes vivent dans accounting/settlements.py
- Date : 2026-11-03 · Statut : adoptée (plan de la tâche 1.6 validé par Mathis)
- Contexte : chaque application range sa logique métier dans `services.py`, mais `accounting/services.py` est réservé aux calculs purs, sans base (règle 3).
- Décision : l'enregistrement et l'annulation des règlements sont dans `accounting/settlements.py`, qui lit le solde par `accounting/queries.py` et les refus par `accounting/services.py`.
- Alternatives écartées : les mettre dans `accounting/services.py` (il toucherait à la base) ; dans `core` (les règlements appartiennent aux comptes).

## D57 — Ce qu'est une dépense du garage, et son montant affiché
- Date : 2026-11-03 · Statut : adoptée (plan de la tâche 1.6 validé par Mathis) ; précise D42
- Contexte : une dépense peut mêler des lignes sur un véhicule et des lignes sans véhicule (commande partagée), avec des frais de port communs. Le montant payé n'est donc pas toujours celui du garage.
- Décision :
  - Une dépense du garage a au moins une ligne sans véhicule. La page « Dépenses du garage » (`/comptes/depenses-du-garage/`, second onglet de « Comptes », D52) n'en montre que ces lignes, au coût net : total réparti, frais de port compris, moins les remboursements. Une commande partagée le dit (« seule la part du garage est comptée ici »).
  - Une dépense est rangée dans l'année de sa date, remboursements compris (même s'ils arrivent l'année suivante). Un sélecteur propose les années qui ont une dépense du garage, plus l'année en cours ; l'année en cours est affichée par défaut.
  - La page donne le total de l'année et ce qu'il représente pour chacun (la moitié, `split_equally`). Elle n'ajoute rien au solde : ces dépenses y sont déjà, par leurs paiements.
  - « Ajouter une dépense du garage » (seul bouton bleu) ouvre le formulaire de dépense habituel (`?depuis=garage`), qui ramène à la liste, à l'année de la dépense. Si la dépense enregistrée n'a aucune ligne sans véhicule, elle n'apparaîtrait pas dans la liste : on ouvre alors la dépense elle-même.
- Alternatives écartées : afficher le montant payé de la dépense entière (faux pour une commande partagée) ; ranger un remboursement dans l'année où il arrive (plus juste en trésorerie, mais une même dépense apparaîtrait sur deux années).
- Conséquences : `ExpenseInput.date` ; `accounting.services` (`is_garage_expense`, `garage_expenses_for_year`, `garage_expense_years`) ; `accounting.queries.garage_expenses` ; `expenses.views.expense_create` (`?depuis=garage`). Le bilan (sous-étape 3) rangera ces dépenses de la même façon.

## D58 — Achats personnels dans « Dépenses du garage »
- Date : 2026-11-03 · Statut : adoptée (plan de la tâche 1.6 validé par Mathis) ; précise D09
- Décision : les achats personnels de l'année sont listés à part, dans un bloc grisé « Achats personnels (hors comptes) », avec leur montant payé pour mémoire. Ils ne comptent ni dans le total de l'année, ni dans la part de chacun, ni dans le solde.
- Alternatives écartées : les mêler à la liste avec un badge (le total de la page ne correspondrait plus aux lignes affichées) ; ne pas les montrer (le cahier des charges veut les garder pour mémoire).

## D59 — Perte d'un véhicule abandonné
- Date : 2026-11-03 · Statut : adoptée (plan de la tâche 1.6 validé par Mathis)
- Contexte : un véhicule abandonné n'a pas de vente, donc pas de marge (D32), mais l'argent dépensé est perdu.
- Décision :
  - Perte = coût de revient − acomptes conservés (même formule que la marge, avec un prix de 0 €). Elle est partagée moitié chacun, comme une marge négative.
  - Elle compte dans l'année du dernier passage en « Abandonné » (`StatusChange`). Un véhicule repris de l'Excel déjà abandonné, sans changement de statut enregistré, prend la date de création de sa fiche.
  - Rien n'est stocké : si le véhicule revient au garage, la perte disparaît ; si une pièce en est transférée (étape 2), la perte baisse d'elle-même.
  - Un véhicule qui a une vente non annulée est rangé parmi les vendus, quel que soit son statut (D22). Une fiche à la corbeille dont les dépenses ne le sont pas garde son coût, compté avec l'argent encore dans le garage.
- Alternatives écartées : dater la perte de l'achat (une perte de 2026 sur un achat de 2025 fausserait 2025) ; enregistrer la perte au moment de l'abandon (un chiffre à tenir à jour à la main).
- Conséquences : `accounting.services.vehicle_report` ; `accounting.queries.vehicle_states`.

## D60 — Bénéfice d'une année
- Date : 2026-11-03 · Statut : adoptée (plan de la tâche 1.6 validé par Mathis) ; c'est le « vrai bénéfice » annoncé par D38
- Décision : bénéfice de l'année = marges des ventes de l'année − pertes des abandons de l'année − dépenses du garage de l'année (D57), moitié chacun (`split_equally`). Le bilan de l'année donne aussi le total investi (coût net des dépenses non personnelles datées de l'année, véhicules et garage), le total des ventes non annulées (reprise comprise) et la durée moyenne de l'achat à la vente, arrondie au jour.
- Contrôle « chaque euro est rangé » : somme des positions des associés = argent encore dans le garage − bénéfice cumulé. Vérifié par les tests et par `controle_comptes`.
- Lecture à l'écran (après l'essai de la sous-étape 4, 2026-11-04) : « Investi » compte l'argent dépensé dans l'année, alors que le tableau de l'année montre le coût complet des véhicules vendus ou abandonnés dans l'année. Un véhicule acheté en 2025 et abandonné en 2026 est donc investi en 2025 et perdu en 2026. Sous le tableau, un détail fait le lien : coût des véhicules du tableau + dépenses du garage de l'année + dépensé dans l'année pour d'autres véhicules − dépensé une autre année pour ceux du tableau = « Investi » (`invested_breakdown`, testé pour chaque année). Une marge qui compte des acomptes conservés le dit sous la ligne (« dont 400,00 € d'acompte conservé ») ; une perte aussi (« après … »).
- Conséquences : `accounting.services.year_report`, `money_buckets`, `buckets_problems`, `invested_breakdown`.

## D61 — Le bilan d'un associé, en une addition
- Date : 2026-11-03 · Statut : adoptée (chiffres de la vraie base montrés à Mathis avant et après un règlement, formulation validée) ; remplace la proposition du plan (investi en cours, reste à récupérer, déjà récupéré, bénéfice gagné)
- Contexte : le cahier des charges demande un bilan par associé sans le définir. La première proposition donnait des chiffres difficiles à lire tant que le solde n'était pas réglé.
- Décision : pour chaque associé, un tableau qui se lit comme une addition, avec des libellés neutres :
  - « Mis dans le garage » = dépenses payées − remboursements reçus ;
  - « Part du bénéfice » = sa moitié du bénéfice cumulé (`split_equally`) ;
  - « Ce qui lui revient » = mis dans le garage + part du bénéfice ;
  - « Déjà revenu » = ventes, acomptes et reprises encaissés + règlements reçus − règlements versés ;
  - « Encore dans le garage » = sa moitié de l'argent encore dans le garage, c'est-à-dire le coût des véhicules au garage moins les acomptes en attente (en cours, ou conservés sur un véhicule ni vendu ni abandonné) ;
  - « Solde à régler » : ce qu'il doit verser ou recevoir.
  - Ce qui lui revient = déjà revenu + encore dans le garage + solde à recevoir (ou − solde à verser). L'écart d'arrondi éventuel (au plus un centime, D33) va sur la ligne « Encore dans le garage », pour que l'addition tombe juste.
- La règle de la moitié ne change pas : le bénéfice et l'argent encore dans le garage sont partagés moitié chacun. Une fois le solde réglé, « Déjà revenu » égale « Ce qui lui revient » moins « Encore dans le garage ». Celui qui a versé un règlement sans avoir rien encaissé a un « Déjà revenu » négatif : il a sorti cet argent de sa poche.
- Contrôle : `controle_comptes` affiche cette addition pour chaque associé et signale un écart de plus d'un centime.
- Affichage dans le bilan (décision de Mathis après l'essai de la sous-étape 3, 2026-11-03) : aucun nombre négatif seul.
  - « Déjà revenu » est détaillé en deux lignes : « Encaissé (ventes, acomptes, reprises) » et « Règlements », écrits « reçu X € » ou « versé X € » (reçus − versés), « Aucun » à zéro.
  - Le total « Déjà revenu » reste en dessous, avec une phrase simple : « Déjà récupéré par Mathis : encaissé, plus règlements reçus, moins règlements versés. » S'il est négatif, il s'écrit « Rien pour l'instant » avec la phrase « Axel a versé X € de plus que ses encaissements : cet argent est encore à récupérer. »
  - Une part du bénéfice négative s'écrit « Perte de X € », sans signe, comme partout ailleurs ; « Ce qui lui revient » négatif s'écrit « Rien : sa part de la perte dépasse sa mise de X € » ; « Encore dans le garage » négatif (acompte reçu sur un véhicule qui n'a encore rien coûté) s'écrit « Rien : X € d'acomptes reçus d'avance » ; le solde s'écrit « doit verser X € », « doit recevoir X € » ou « À l'équilibre ». La marge des ventes et le résultat des abandons de l'année passent aussi par « Perte de X € ».
  - Les montants qui peuvent être négatifs passent par `accounting.services.SignedAmount` ; le filtre `resultat` écrit « Perte de X € ».
- Alternatives écartées : la proposition du plan (« déjà récupéré » pouvait être négatif pour celui à qui l'on doit, et comptait une perte comme de l'argent récupéré) ; un calcul qui suppose le solde déjà réglé (il ne collerait pas avec l'argent réel).
- Conséquences : `accounting.services.partner_report`, `partner_report_problems` ; `accounting.queries.load_report`.

## D62 — Argent encore dans le garage, aujourd'hui seulement
- Date : 2026-11-03 · Statut : adoptée (plan de la tâche 1.6 validé par Mathis)
- Décision : l'argent encore dans le garage (véhicules au garage, moins les acomptes en attente) est donné pour aujourd'hui, pas pour la fin des années passées.
- Alternatives écartées : le reconstituer au 31 décembre de chaque année (il faudrait rejouer l'historique des statuts et des dépenses, pour peu d'intérêt).

## D63 — Tuile « Bénéfice <année> » sur l'accueil
- Date : 2026-11-03 · Statut : adoptée (plan de la tâche 1.6 validé par Mathis) ; remplace D38
- Décision : la tuile « Marge des ventes <année> » devient « Bénéfice <année> » : le bénéfice de l'année de D60 (marges − pertes des abandons − dépenses du garage). Une perte s'écrit « Perte de X € ». Toute la tuile est un lien vers le bilan (« Voir le bilan »).
- Conséquences : `accounting.queries.garage_overview` donne `profit` (calculé par `year_report`) ; `sales_margin_for_year` est retirée, le bilan la remplace.

## D64 — PDF du bilan reporté à l'étape 3
- Date : 2026-11-03 · Statut : adoptée (plan de la tâche 1.6 validé par Mathis) ; **écart avec le cahier des charges**, qui veut un bilan exportable en PDF
- Contexte : l'outil de génération des PDF se choisit à l'étape 3, avec le dossier de vente (D20).
- Décision : la 1.6 n'exporte le bilan qu'en Excel (sous-étape 5). Le PDF du bilan viendra avec le dossier de vente, en réutilisant le même outil.
- Alternatives écartées : choisir un outil PDF dès maintenant pour le seul bilan (deux outils possibles, installation sous Windows à vérifier) ; l'impression du navigateur (mise en page non maîtrisée).

## D65 — Contenu de l'export Excel
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.6 validé par Mathis)
- Décision :
  - Bouton « Exporter en Excel » sur le bilan (seule action de la page, donc bleu). Il télécharge `MAx Engine - export du AAAA-MM-JJ.xlsx`, fabriqué par `openpyxl` dans `accounting/export.py`, en lecture seule et sans transaction : il marche aussi en mode lecture seule (D48).
  - Feuilles du bilan : par véhicule (situation, investi par chacun, coût, vente, acomptes conservés, marge ou perte, durée, date d'abandon), par année (investi, ventes, marges, pertes, dépenses du garage, bénéfice, part de chacun, durée moyenne), par associé (D61) et solde (comme l'écran « Comptes »).
  - Puis une feuille par table, toutes les données du logiciel (le cahier des charges veut un export complet ; le plan n'en citait qu'une partie), corbeille incluse avec sa colonne « Mis à la corbeille le » : véhicules, dépenses, lignes, paiements, remboursements, transferts de pièce, ventes, encaissements, acomptes, règlements, changements de statut, interventions, choses à faire, annonces comparables, fichiers joints (leur chemin dans `media/`, pas le fichier), contacts, fournisseurs, associés, types de véhicule, origines d'achat, catégories de dépense, paramètres. Un lien vers une dépense, une ligne, une vente ou un règlement est écrit par son numéro (colonne « (n°) ») ; les autres liens par leur nom.
  - Que des valeurs, aucune formule : les montants sont de vrais nombres écrits depuis des `Decimal`, au format euro (une perte en rouge, avec son signe : c'est un tableur, pas l'écran) ; les dates sont de vraies dates ; un texte qui commence par « = » reste un texte.
  - Le fichier contient les coordonnées des contacts : il reste sur le portable et n'est envoyé nulle part.
- Alternatives écartées : des formules Excel (les chiffres pourraient différer de l'écran) ; exclure la corbeille (l'export sert aussi de sauvegarde lisible).

## D66 — Coût des véhicules au garage : une seule source, un seul libellé
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis)
- Contexte : la relecture de la 1.6 a trouvé trois libellés pour deux chiffres. L'accueil affichait « Investi au garage » (somme des cartes, fiches à la corbeille exclues), le bilan « Coût des véhicules au garage » (fiches à la corbeille comprises). Les deux écrans pouvaient donc montrer deux montants différents.
- Décision :
  - L'accueil lit le chiffre du bilan (`MoneyBuckets.vehicles_in_garage`, fiches à la corbeille comprises) et l'appelle comme lui « Coût des véhicules au garage ». Quand une fiche à la corbeille porte encore de l'argent, les deux écrans ajoutent « dont X € sur des fiches à la corbeille ».
  - « Investi » veut toujours dire « argent dépensé » : « Investi en <année> » (bilan), « Investi par Mathis » et « Total investi » (une carte, une fiche). Le coût des véhicules au garage n'est plus appelé « investi ».
- Alternatives écartées : exclure la corbeille des deux côtés (l'argent d'une fiche à la corbeille disparaîtrait du bilan, et « chaque euro est rangé » tomberait faux).
- Conséquences : `accounting.queries.garage_overview(year)` donne `garage_cost` et `in_trash`, et lit le bilan une seule fois (les coûts des cartes viennent de ses lignes) ; `accounting.services.invested_total` est retirée.

## D67 — Une rubrique, un libellé
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis)
- Décision :
  - Les encaissements d'un associé s'appellent partout « Encaissé (ventes, acomptes, reprises) » : écran « Comptes », bilan, feuilles « Solde » et « Bilan par associé » de l'export.
  - Le solde nul s'écrit « À l'équilibre » quand il est une valeur (grand chiffre de « Comptes », bloc Vente de la fiche, bilan par associé, feuille « Solde ») : `accounting.services.balance_label`. Dans une phrase, il s'écrit « Vous êtes à l'équilibre. » (message après un règlement) ou « vous serez à l'équilibre » (aperçu).
  - `controle_comptes` garde son écriture (« comptes à l'équilibre », `balance_sentence`) : ses comptes rendus d'avant et d'après une modification restent comparables ligne à ligne.

## D68 — La marge n'a qu'un chemin de calcul
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis)
- Contexte : la marge était calculée deux fois, par `queries.vehicle_margins` (cartes « Vendus », `controle_comptes`) et par `services.vehicle_report` (bilan). Les deux donnaient le même résultat, mais une correction faite d'un seul côté aurait fait diverger les écrans.
- Décision : la marge vient seulement du bilan par véhicule (`services.report_margins`, qui lit les lignes de `vehicle_report`). `queries.vehicle_margins(report)` et `sold_margins` la lisent là. `controle_comptes` lit le bilan une seule fois.
- Corrections faites en même temps (relecture de la 1.6) :
  - `queries.vehicle_states` lit aussi les fiches à la corbeille (`VehicleState.in_trash`) : une fiche vendue mise à la corbeille garde sa date d'achat, donc sa durée avant la vente.
  - Un règlement lit les comptes deux fois au lieu de trois : à l'ouverture du formulaire, et dans la transaction (D53). Le message de confirmation reprend le solde calculé dans la transaction (`settlements.RecordedSettlement`).
  - Export : le format euro est réservé aux champs d'argent (`export.est_montant`) ; les heures d'une intervention et les quantités sont des nombres simples (`General`).
- Vérification : `controle_comptes` sur la base d'essai donne le même compte rendu avant et après.

## D69 — Pages d'erreur en français
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis)
- Contexte : sans DEBUG, ce qui sera le cas avec le lanceur (D26), Django affichait « Not Found » ou « Server Error (500) » en anglais, sans lien pour revenir.
- Décision :
  - `templates/404.html` : « Cette page n'existe pas (ou plus). », une phrase d'explication (lien ancien, élément à la corbeille) et le bouton « Revenir à l'accueil ». Elle garde la barre latérale.
  - `templates/500.html` : « Quelque chose s'est mal passé. », avec quoi faire (fermer et rouvrir MAx Engine, prévenir Mathis sinon). Elle ne lit ni la base ni la session : elle s'affiche même quand la base pose problème.
- Conséquences : `handler404 = core.views.page_not_found` ; un fichier statique ou joint introuvable (`/static/`, `/media/`) reçoit une réponse courte « Fichier introuvable. », sans fabriquer la page. Sous `/static/`, aucune requête n'est faite ; sous `/media/`, le middleware du choix de l'associé lit encore la session (et renvoie vers « Qui es-tu ? » sans associé choisi) : à revoir avec la façon de servir `media/` (D85). Les deux pages se voient seulement sans DEBUG (`manage.py` l'active en développement) ; les tests forcent `DEBUG = False`.

## D70 — Dossier des sauvegardes
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; mise en œuvre en sous-étape 2
- Contexte : la base ne vit que sur le portable d'Axel (D04) ; une panne ou un vol ferait tout perdre.
- Décision : les sauvegardes vont dans le dossier OneDrive d'Axel partagé avec Mathis (par exemple `%OneDrive%\MAx Engine - sauvegardes`). Le chemin est rangé dans `GarageSettings.backup_dir` (les variables comme `%OneDrive%` y sont comprises), et la variable `BACKUP_DIR` passe avant lui. Sans l'un ni l'autre, elles vont dans `sauvegardes/` du projet, avec un avertissement.
- Conséquences : un dossier réglé mais absent (OneDrive fermé, pas encore synchronisé) n'est pas créé : la sauvegarde échoue avec un message clair, plutôt que d'écrire hors de OneDrive sans le dire. Les sauvegardes de test ne vont jamais dans ce dossier.

## D71 — Une sauvegarde : un zip pour la base, un miroir pour les fichiers joints
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; mise en œuvre en sous-étape 2
- Décision : chaque sauvegarde est un fichier `MAx Engine AAAA-MM-JJ HH-MM (raison).zip` (`(manuelle - nom)` pour une sauvegarde nommée) qui contient `garage.sqlite3`. Les photos et factures sont recopiées une seule fois dans le dossier miroir `fichiers/` : seuls les nouveaux fichiers sont ajoutés, aucun n'est modifié ni effacé.
- Pourquoi : trente copies complètes des photos (jusqu'à 20 Mo chacune) rempliraient vite les 5 Go gratuits de OneDrive ; un fichier joint ne change jamais et n'est jamais effacé (corbeille logique).
- Sûreté (`core/backup.py`) : la base est lue par une connexion à part, en lecture seule, avec l'API de sauvegarde de SQLite (copie cohérente même logiciel ouvert, D04), avec un délai d'attente de 10 secondes si elle est occupée. La copie se fait d'abord dans un dossier temporaire du PC, puis elle est contrôlée (`PRAGMA integrity_check`, nombre de véhicules et de dépenses, la corbeille comptée à part comme dans `controle_comptes`). Une base absente est refusée avant toute requête (Django créerait sinon un fichier vide à sa place). Le zip est écrit sous un nom en `.tmp`, contrôlé, puis renommé : OneDrive ne voit jamais une sauvegarde à moitié écrite. Deux sauvegardes de la même minute et de la même raison sont numérotées (« 18-02 2 ») ; le renommage final n'écrase jamais un fichier existant. Toute erreur, même imprévue, donne un message en français et est notée comme un échec.

## D72 — Rotation des sauvegardes
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; mise en œuvre en sous-étape 2
- Décision : on garde les 30 dernières sauvegardes automatiques (fermeture et quotidiennes comptées ensemble) et les 10 dernières « mise-a-jour ». Les sauvegardes manuelles ne sont jamais effacées automatiquement, ni aucun fichier dont le nom n'est pas celui d'une sauvegarde. La sauvegarde qui vient d'être écrite compte dans le total, mais n'est jamais effacée, même si l'horloge du PC a reculé. Un nom (`--nom`) ne se donne qu'à une sauvegarde manuelle : une « fermeture » nommée partirait avec la rotation.

## D73 — Quand sauvegarder, comment restaurer
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; commande en sous-étape 2, branchement en sous-étape 7
- Décision : une sauvegarde à chaque fermeture, au lancement s'il n'y en a pas eu depuis 24 heures (`--raison quotidienne` ne fait rien sinon), avant chaque mise à jour, et à la main. La restauration n'a pas d'écran : la marche à suivre est dans `README.md` et faite par Mathis.
- Conséquences : le résultat de la dernière sauvegarde (réussite, échec et son message) est noté dans `derniere-sauvegarde.json`, à côté de la base, et non dans la base : la commande n'y écrit jamais. En bas de la barre latérale, « Dernière sauvegarde : aujourd'hui à 18:02 » passe en ambre après 2 jours, sans aucune sauvegarde, ou après un échec (« La dernière sauvegarde a échoué : préviens Mathis. »). L'indicateur est masqué en lecture seule ; menu replié, il reste une icône d'alerte. L'ambre vient de `core/ui.py` (`WARNING_BADGE`), comme « À commander ». Un fichier d'état illisible vaut « aucune sauvegarde », sans jamais faire planter une page. Un dossier réglé qui n'est pas un chemin complet est refusé.

## D74 — Ce qu'on met à la corbeille depuis l'écran
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; mise en œuvre en sous-étape 3a
- Décision : en plus des photos, factures, pièces à commander et remboursements, on met à la corbeille :
  - **une dépense entière**, depuis sa fiche (« Mettre à la corbeille », avec confirmation). Ses lignes, paiements, remboursements et factures restent attachés et sortent des comptes avec elle. C'est ainsi qu'on annule un achat saisi par erreur (D35, D39). Refusée si elle est verrouillée (D50), ou si c'est l'achat d'un véhicule repris par une vente en cours (D51) : le bouton n'est alors pas proposé ;
  - **une fiche sans argent** (créée par erreur) : ni dépense payée, ni pièce à commander, ni acompte, ni vente (même terminés ou annulés), ni transfert de pièce. Le bas de la fiche propose « Mettre la fiche à la corbeille », ou dit ce qu'il faut faire avant (retirer ses pièces à commander). Une fiche qui a de l'argent (dépense payée, vente, acompte) ou qui est une reprise ne parle pas de corbeille : le proposer inviterait à effacer de vraies dépenses (relecture du 2026-11-08). Le service garde le refus « Mets d'abord ses dépenses à la corbeille » pour une page restée ouverte ;
  - **un contact**, à la sous-étape 5 (la corbeille sait déjà le restaurer).
- Conséquences : `expenses.services.trash_expense`, `vehicles.services.trash_vehicle` ; chacun relit la base dans sa transaction et refuse un second clic (« déjà dans la corbeille »). Lien « Corbeille » en bas de la barre latérale.

## D75 — Règles de restauration
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; mise en œuvre en sous-étape 3a
- Décision : la page « Corbeille » (`/corbeille/`) liste par groupe (Véhicules, Dépenses, Pièces à commander, Remboursements, Photos et factures, Contacts) ce qui y est, quand et par qui (lu dans l'historique quand il existe). Chaque élément a « Restaurer », ou, à sa place, la raison du refus et quoi faire. Le service (`core/trash.py`) refuse de même, relu dans sa transaction :
  - le verrou est respecté : rien ne revient sur un véhicule vendu non rouvert (dépense, facture, remboursement) ; les photos, permises sur un véhicule vendu, reviennent ;
  - rien ne revient sur le véhicule repris d'une vente annulée, et ni sa fiche ni son achat ne reviennent seuls (D51). Limite acceptée : une vraie dépense de ce véhicule, mise à la corbeille avant l'annulation, ne revient plus ; le message dit de prévenir Mathis ;
  - un élément posé sur un véhicule à la corbeille attend que le véhicule soit restauré ; une facture ou un remboursement attend sa dépense ;
  - un achat ne revient pas si le véhicule en a déjà un autre (D39), ni si le véhicule est revenu en « En attente d'achat » : on enregistre alors de nouveau l'achat. Restauré, il redonne sa date d'achat au véhicule ;
  - une pièce à commander ne revient pas sur un véhicule vendu ou abandonné (D50) ;
  - un remboursement ne revient que si la ligne peut encore être remboursée de ce montant ; la pièce redevient alors « Retourné » si elle l'est entièrement (D45) ;
  - un paiement, un encaissement (D40, D49) et une ligne retirée d'une dépense payée ne sont pas listés : ils ne reviennent jamais seuls (une ligne seule changerait les frais de port).
- Conséquences : il n'y a pas de bouton « Vider la corbeille ». Restaurer une dépense remet les comptes exactement comme avant sa mise à la corbeille (testé avec `controle_comptes`).

## D76 — Ce qui est attaché à une fiche à la corbeille
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; mise en œuvre en sous-étape 3a ; corrige D51
- Décision : les photos, interventions, tâches et annonces d'une fiche à la corbeille (fiche sans argent, ou véhicule repris d'une vente annulée) restent attachées à elle, cachées avec elle : elles ne sont pas mises à la corbeille une à une, et reviennent avec la fiche. Un transfert de pièce (`PartTransfer`) qui touche le véhicule repris bloque l'annulation de la vente, comme pour une fiche : il porte de l'argent d'un véhicule à l'autre.

## D77 — Une ligne sur un véhicule à la corbeille
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; mise en œuvre en sous-étape 3a
- Décision : `save_expense` et les pièces à commander refusent une ligne posée sur un véhicule à la corbeille, dans les services et pas seulement dans le formulaire. Dans « Modifier la dépense », une ligne déjà posée sur un tel véhicule l'affiche « n°X … (à la corbeille) » au lieu de passer en silence sur « Garage » ; l'enregistrement est refusé : « Le véhicule n°X … est à la corbeille : restaure-le ou choisis un autre véhicule. Rien n'est enregistré. » La sortir de ce véhicule reste possible.

## D78 — Journal des modifications
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; mise en œuvre en sous-étape 3b
- Décision : le journal (`/journal/`) lit l'historique des dix tables qui en ont un : véhicules, dépenses, lignes, paiements, remboursements, transferts de pièce, ventes, encaissements, acomptes, règlements. Les contacts, photos, interventions, tâches et annonces n'en ont pas : leur en donner un demanderait une migration, non proposée maintenant ; la page le dit.
  - Chaque enregistrement devient une phrase : verbe (« a ajouté », « a modifié », « a mis à la corbeille », « a restauré », « a rouvert », « a verrouillé », « a annulé »), objet, puis chaque champ changé en « avant → après », écrit comme à l'écran. Les champs techniques (dates de création et de modification, numéro) ne sont pas montrés.
  - L'auteur vient de D30 ; sans associé (commande, import), « Sans associé (commande) ».
  - Les enregistrements du même associé, sur le même sujet (une dépense avec ses lignes, paiements et remboursements ; une vente avec ses encaissements ; une fiche), écrits à moins de 2 secondes d'écart, forment une entrée : c'est un seul clic. Un enregistrement qui ne change rien de visible (une ligne réenregistrée telle quelle) n'est pas montré.
  - Filtres : un véhicule (lu dans l'historique : une ligne passée par ce véhicule puis déplacée en fait partie ; les règlements, qui ne concernent aucun véhicule, n'y sont pas) ou une dépense. Pages de 50 enregistrements, du plus récent au plus ancien.
  - La fiche véhicule montre ses réouvertures (D50), lues dans l'historique de `Vehicle` : date, associé, motif, date du verrouillage suivant.
- Conséquences : `core/journal.py` ne fait que lire. Un objet cité est lu corbeille comprise ; le lien vers une dépense ou une fiche à la corbeille n'est pas proposé. Sur un véhicule repris, le filtre montre la vente dont il est la reprise, mais pas les encaissements de l'autre véhicule. Une vente annulée sur une fiche rouverte n'est pas un « verrouillage » : le bloc « Réouvertures » dit « la vente a été annulée ».
- Limites acceptées (relecture du 2026-11-08) : le regroupement ne se fait que sur un même sujet, donc un clic qui touche deux sujets donne deux entrées (« a annulé la vente » puis « a modifié la fiche · Statut : Vendu → En vente » ; « a ajouté la dépense » de l'achat puis « a modifié la fiche · Acheté le ») ; à trancher par Mathis après son essai. Un clic coupé à la limite de deux pages apparaît sur les deux.

## D79 — Mode lecture seule pour la consultation
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; mise en œuvre en sous-étape 4
- Décision : la seule variable `READ_ONLY=1` met le logiciel en consultation :
  - la base est ouverte en lecture seule par SQLite lui-même (adresse `file:…?mode=ro`), sans le mode `IMMEDIATE` (D48), qui réserverait l'écriture et échouerait ;
  - les sessions passent en cookie signé (elles s'écrivent sinon dans la base) ; il n'y a pas de choix d'associé ;
  - seules les pages de consultation s'ouvrent, par une liste fermée (`core.middleware.READ_ONLY_PAGES`) : tout envoi, HTMX compris, et toute autre page (formulaires, aperçus, administration, « Qui es-tu ? ») donnent une page « Rien ne peut être modifié ici » (code 403). Une liste fermée refuse d'office une page ajoutée plus tard ;
  - l'export Excel reste possible (D65) ;
  - un bandeau en haut de chaque page donne la date de la sauvegarde consultée, lue dans le nom du fichier (`READ_ONLY_SOURCE`, D71) ;
  - les gabarits masquent les actions (drapeau `read_only`, D55).
- Conséquences : la base n'est jamais écrite, testé sur un fichier réellement en lecture seule ouvert dans un processus à part. La ligne « Dernière sauvegarde » n'est pas affichée en consultation.

## D80 — Consulter MAx Engine depuis le PC de Mathis
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; script écrit en sous-étape 7
- Décision : le script « Consulter MAx Engine » copie la dernière sauvegarde du dossier OneDrive partagé dans un dossier temporaire local, l'extrait, puis lance le logiciel avec `READ_ONLY=1`, `DATABASE_PATH` sur cette copie et `READ_ONLY_SOURCE` sur le nom du fichier, sur le port 8001. Il n'ouvre jamais un fichier directement dans OneDrive et ne gêne pas le serveur de développement (port 8000).
- À faire en sous-étapes 6 et 7 (relecture du 2026-11-08) : la consultation lit les photos et factures de la sauvegarde (dossier miroir `fichiers/`, D71) grâce à `MEDIA_PATH`, jamais le `media/` local de Mathis ; et, sans DEBUG, les fichiers statiques et joints doivent être servis (D85).
- Précision (sous-étape 7) : le miroir `fichiers/` est celui du jour, pas celui de la date de la sauvegarde consultée. Il ne fait qu'ajouter des fichiers (D71) : une ancienne sauvegarde y retrouve toutes ses photos, sans danger puisque tout est en lecture seule. Le script refuse de démarrer si le port 8001 est déjà pris, ou si l'ancienne copie ne peut pas être remplacée.

## D81 — Liste des contacts
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; mise en œuvre en sous-étape 5
- Décision : la page « Contacts » permet de chercher (nom, ville, téléphone, aussi sans espaces ni points), d'ajouter, de modifier et de mettre à la corbeille un contact. Chaque contact montre ses rôles, lus dans les fiches (vendeur), les ventes (acheteur, vente annulée comprise) et les acomptes (sauf celui déduit de la vente du même acheteur). Pas de fusion des doublons : elle pourra venir si le besoin apparaît.
  - La corbeille ne détache pas un contact de ses véhicules ; sur une fiche, il est nommé « (à la corbeille) », sans lien, et reste choisi dans les listes avec la même mention.
  - Les contacts n'ont pas d'historique (D78) : leurs modifications n'entrent pas au journal.
  - La recherche compare sans majuscules ni accents, en Python, comme les fournisseurs (D43) : le `LIKE` de SQLite ne trouvait pas « Éric » en tapant « eric ». La liste est triée de même.
  - Ajouter (ou renommer) un contact du même nom qu'un autre affiche un avertissement, qui reste jusqu'à ce qu'on le ferme : « Un autre contact s'appelle déjà … : vérifie que ce n'est pas la même personne. »
- Conséquences : `core/contacts.py` (recherche, rôles en trois requêtes, `trash_contact` relu dans sa transaction) ; `ContactEditForm` (fiche complète, seul le nom est obligatoire) ; la liste et la fiche sont ouvertes en consultation (`READ_ONLY_PAGES`).

## D82 — L'import de l'Excel ne se fait que sur une base vide, sans option pour forcer
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; mise en œuvre en sous-étape 6. Écart avec l'ancienne version de `docs/import-excel.md` (option `--forcer`), mise à jour. **Modifiée par D94** (2.0.1) : les valeurs de contrôle ne sont plus écrites dans le code, elles sont lues dans « Bilan & Comptes ».
- Décision : `importer_excel` refuse une base qui contient déjà un véhicule, une dépense, une ligne ou pièce à commander, un paiement, un remboursement, un transfert, une vente, un encaissement, un acompte ou un règlement, corbeille comprise. Pas d'option `--forcer`. L'import se fait en une transaction, par les services de l'écran, et se contrôle contre les valeurs relevées dans l'Excel (Mathis 1 564,15 €, Axel 1 153,27 €, total 2 717,42 €, « Axel doit 205,44 € à Mathis », coût de chaque moto) ; au moindre écart, rien n'est importé.
- Pourquoi : les motos doivent porter les n°1 à 3, et le contrôle des 205,44 € n'a de sens que sur une base vide. Rien ne distingue une base de test sur le portable d'Axel : une option pour forcer pourrait doubler les comptes. Les tests appellent la fonction d'import sur leur base vide.
- Conséquences : `core/excel_import.py`, `importer_excel` ; la répétition se fait sur une base vide à part (`DATABASE_PATH`), et la variable `MEDIA_PATH` est ajoutée pour que ses fichiers joints n'aillent jamais dans le vrai `media/`. Les contacts et les listes modifiables ne bloquent pas l'import : ils ne portent pas d'argent.

## D83 — Une ligne de l'Excel sans statut est « Reçu » ; marque et modèle au premier mot
- Date : 2026-11-04 · Statut : adoptée, **modifiée par Mathis** (le plan proposait « Commandé ») ; mise en œuvre en sous-étape 6 ; remplace la règle « vide → `ORDERED` » du 2026-10-27
- Décision : une ligne du journal sans statut devient « Reçu » (`RECEIVED`), car les achats de l'Excel sont déjà faits. « A commander » sur une ligne payée devient « Commandé » (`line_status`). Le nom de la moto se découpe au premier mot : marque « YAMAHA », modèle « YZ250 » ; marque « GG », modèle « MC250 ». La casse de l'Excel est gardée.
- Conséquences : Axel confirme les statuts et corrige marque et modèle le jour de l'installation (sous-étape 7). L'import refuse un véhicule vendu ou abandonné : il se saisit à la main.

## D84 — Lanceur Windows
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; mise en œuvre en sous-étape 7
- Décision : le raccourci « MAx Engine » lance `pythonw lanceur.py` (`core/launcher.py`), sans terminal et sans `manage.py` : DEBUG reste faux (D26).
  - La base doit exister et ses migrations être faites, sinon un message dit de lancer « Mettre à jour MAx Engine » : Django ne crée jamais une base vide à la place d'une base disparue.
  - Waitress écoute sur `127.0.0.1:8000` seulement. Edge s'ouvre en mode application (`--app`) avec son propre profil (`donnees/edge-profil`).
  - Edge relance en fait un autre processus : la fermeture de la fenêtre se lit au verrou du profil (`lockfile`, ouvert par Edge tant qu'il tourne), pas à la fin du processus lancé.
  - Une sauvegarde « quotidienne » part au lancement s'il n'y en a pas eu depuis 24 heures ; la sauvegarde « fermeture » se fait quand la fenêtre est fermée, puis le programme s'arrête (D73). Un échec de la sauvegarde de fermeture s'affiche, en rassurant : les saisies sont bien enregistrées.
  - Un second double-clic reconnaît MAx Engine par l'adresse `/sante/` (« MAx Engine <version> ») et rouvre seulement une fenêtre. Un autre logiciel sur le port donne un message clair.
  - Toute erreur s'affiche dans une boîte de message Windows en français ; le détail va dans `donnees/lanceur.log` (remis à zéro au-delà de 1 Mo).
  - `--consultation --port 8001` sert à la consultation en lecture seule (D80) : il exige `READ_ONLY=1`, ne sauvegarde pas et prend un profil Edge à part, dans le dossier temporaire.
- Conséquences : `/sante/` est ouverte sans choix d'associé et en consultation. `core/version.py` donne la version affichée (0.1.0 jusqu'à la publication de la 1.0.0, faite après l'essai de Mathis).

## D85 — Fichiers statiques et joints servis par Django, sans DEBUG
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; mise en œuvre en sous-étape 7 ; tranche le choix laissé ouvert par D26
- Décision : `config/urls.py` sert toujours `/static/` par la vue de `django.contrib.staticfiles` (`insecure=True`), qui lit les fichiers à leur place dans le projet et les bibliothèques (htmx de `django-htmx`) : pas de `collectstatic` à faire à chaque mise à jour. `/media/` est servi par `django.views.static.serve`, depuis `MEDIA_ROOT` (donc `MEDIA_PATH` en consultation). Un chemin qui sortirait du dossier est refusé.
- Pourquoi : un seul poste, sur `127.0.0.1` ; aucune dépendance de plus. WhiteNoise ne sert pas les fichiers joints.
- Conséquences : les fichiers statiques, les fichiers joints et `/sante/` passent sans choix d'associé (middleware) : une photo ou la page 500 n'ont pas à lire la base. L'icône `static/max-engine.ico` (dessinée par `scripts/construire_icone.py`) sert aux raccourcis et à l'onglet.

## D86 — « Mettre à jour MAx Engine »
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; mise en œuvre en sous-étape 7
- Décision : le raccourci lance `scripts/mettre_a_jour.ps1` dans une petite fenêtre aux messages en français. Il refuse de démarrer si MAx Engine répond sur le port (fenêtre ouverte ou sauvegarde de fermeture en cours), puis, en s'arrêtant à la première erreur :
  1. sauvegarde « mise-a-jour » (avec l'ancien code, avant tout changement) ;
  2. `controle_comptes` avant ;
  3. refus si un fichier suivi par Git a été modifié sur le portable, puis `git pull --ff-only` (jamais de fusion ni d'écrasement) ;
  4. `uv sync --locked --no-dev` ;
  5. `migrate` ;
  6. `controle_comptes` après : les positions des associés, le solde, le bénéfice et l'argent encore dans le garage doivent être identiques à ceux d'avant (le compte rendu avant doit contenir un solde) ;
  7. relance de MAx Engine.
- Messages d'arrêt : avant la sauvegarde, « Rien n'a été changé : préviens Mathis. » ; après, « Rien n'est perdu, la sauvegarde est faite : préviens Mathis. » Sans connexion, le message dit de vérifier Internet et de recommencer.
- Repère (contrôle du gardien des données, 2026-11-13) : juste avant `migrate`, le script pose `donnees/mise-a-jour-a-verifier`, retiré seulement quand les comptes sont retrouvés identiques. Tant qu'il est là, ni le lanceur ni la mise à jour ne démarrent. Sans lui, une relance après l'arrêt « comptes changés » comparait la base faussée à elle-même et finissait en vert, et chaque relance ajoutait une sauvegarde « mise-a-jour » de la base faussée, jusqu'à effacer la bonne (rotation des 10).
- Conséquences : tout le détail (sorties des commandes, comptes avant et après, code Git d'avant) va dans `donnees/mises-a-jour.log`. Pas de retour automatique une fois la base touchée : Mathis revient à l'ancien code (`git reset --hard`, `uv sync`) **avant** de restaurer la sauvegarde « mise-a-jour », sinon `migrate` referait la migration fautive (README, « Revenir à la version d'avant »). Avant `migrate`, un échec de `uv sync` ramène tout seul la version d'avant (D93). Les avertissements de Git ne comptent pas comme fichiers modifiés.

## D87 — Installation chez Axel
- Date : 2026-11-04 · Statut : adoptée (plan de la tâche 1.7 validé par Mathis) ; scripts écrits en sous-étape 7, installation faite avec Mathis
- Décision :
  - le logiciel va dans `C:\MAx Engine\`, hors OneDrive (D04) : sur beaucoup de portables, le Bureau et les Documents sont synchronisés ;
  - Git et uv s'installent par `winget` ;
  - le dépôt privé est lu par une clé de déploiement GitHub propre au portable, sans droit d'écriture (alias SSH `github-max-engine`) ;
  - `scripts/installer.ps1` vérifie l'emplacement et les outils, installe les bibliothèques, crée ou met à niveau la base, règle le dossier des sauvegardes (commande `dossier_sauvegardes`, qui exige un dossier existant donné en chemin complet), puis crée les raccourcis « MAx Engine » et « Mettre à jour MAx Engine ». Il refuse un autre dossier que `C:\MAx Engine` (sauf `-AutreEmplacement`, pour les essais) : lancé dans le dossier de développement, il réglerait la base d'essai sur le dossier des sauvegardes d'Axel. Il peut être relancé : sur une base existante, il sauvegarde (« mise-a-jour ») avant `migrate`. Avec `-Consultation`, il crée seulement le raccourci « Consulter MAx Engine » sur le PC de Mathis.
- Conséquences : la marche à suivre pas à pas (clé de déploiement comprise) est en tête de `installer.ps1`, et la liste à cocher le jour de l'installation dans `README.md`. `core/version.py` est créé ; la version 1.0.0 sera publiée (`/publier-version`) après l'essai de Mathis.

## D88 — « Pour ce véhicule seul » se lit dans le tableau de la vente
- Date : 2026-11-04 · Statut : adoptée (demandée par Mathis après l'essai de la sous-étape 1 de la 1.7) ; précise D49 et D51, ne change pas D33
- Contexte : sur la fiche n°1 de la démo (Yamaha YZ125), le tableau donnait à Axel une part totale de 1 277,59 € (marge impaire de 865,21 € : le centime impair va au premier associé), dont 150,00 € déjà reçus avec la reprise, soit 1 127,59 € à recevoir ; la phrase « Pour ce véhicule seul » disait 1 127,60 €. La phrase suivait la formule du solde (`balance`) : (écart des positions) / 2 = 1 127,595 €, arrondi en faveur de celui qui a avancé (D33). Deux arrondis d'un même demi-centime, chacun juste, mais en désaccord sur le même écran.
- Décision : le règlement d'une vente seule (`services.recap_balance`) se lit dans son tableau : celui qui a reçu moins que sa part totale attend la différence. Le montant tombe juste au centime, sans arrondi. La reprise y compte comme dans la colonne « Encaissé », pour sa moitié (avec une reprise impaire de 700,01 €, le véhicule seul dit 150,00 € et le garage 150,01 €).
- Conséquences : le solde de tout le garage (`balance`, D33) ne change pas, ni aucun compte (`controle_comptes` identique). Le véhicule seul et le garage peuvent différer d'un centime quand la marge ou la reprise est impaire : la phrase « Ce solde compte tout le garage. Pour ce véhicule seul : … » s'affiche alors, avec le chiffre du tableau.

## D89 — Acompte en plusieurs versements
- Date : 2026-11-14 · Statut : adoptée (plan de la sous-étape 1 de la 2.0 validé par Mathis) ; modifie D47
- Contexte : un acompte n'avait qu'un montant et qu'un associé qui l'avait reçu. Un acompte reçu par les deux associés, ou complété plus tard, ne se saisissait pas, ou se saisissait faux (tout chez un seul associé), ce qui faussait le solde.
- Décision :
  - Un acompte a un ou plusieurs versements (`DepositReceipt`) : date, montant, qui l'a reçu. Son montant est leur somme, jamais stocké. Il reste un seul acompte en cours par véhicule (D47).
  - « Enregistrer un acompte » demande qui a reçu l'argent comme « Qui a payé ? » d'une dépense (un associé, « Moitié-moitié », « Montants différents ») : un versement par associé, à la date de l'acompte.
  - « Ajouter un versement » complète un acompte en cours. « Corriger l'acompte » change la date de la réservation et l'acheteur ; chaque versement se corrige à part (montant, date, qui l'a reçu). Un versement retiré part à la corbeille, comme un encaissement (D49), et ne se restaure pas ; le dernier ne se retire pas (on annule alors l'acompte).
  - Un ancien associé reste possible seulement sur un versement qu'il avait déjà reçu.
  - Rendu ou conservé se choisit pour tout l'acompte (choix de Mathis) : rendu, aucun effet ; conservé, chaque versement reste chez celui qui l'a reçu et l'acompte compte dans la marge (D32) ou la perte (D59). Déduit d'une vente, tous les versements le sont.
  - Calculs : les fonctions pures ne changent pas. La lecture de la base (`queries.load_deposits`) donne un `DepositInput` par versement hors corbeille, avec l'état, le véhicule, la vente et le numéro de son acompte. Tout ce qui compte des acomptes (et non de l'argent) les compte par numéro : `controle_comptes` ne voit pas un acompte de deux versements comme deux acomptes.
  - Migration : `sales.0004` crée les versements et renomme le montant et « reçu par » de l'acompte en `legacy_amount` et `legacy_received_by`, devenus facultatifs, sans rien supprimer (l'historique garde les anciennes valeurs). `sales.0005` recopie chaque acompte en un versement, daté comme lui, puis vide les anciens champs ; elle refuse de recopier si des versements existent déjà (l'argent serait compté deux fois). Son retour arrière remet l'ancienne forme. Il refuse s'il existe un acompte de plusieurs versements, un versement à la corbeille, ou une saisie ou une correction d'acompte faite en 2.0 (son historique ne tiendrait pas dans l'ancienne forme, et le retour de `sales.0004` s'arrêterait à mi-chemin). Les soldes ne bougent pas.
  - `controle_comptes` signale un acompte sans versement et un ancien montant resté rempli ; son décompte dit « 1 acompte en cours (2 versements) » quand il y en a plus que d'acomptes.
- Alternatives écartées : plusieurs acomptes en cours par véhicule (rendu ou conservé à donner pour chacun, moins clair pour Axel) ; un choix rendu ou conservé par versement (plus de code d'argent pour un cas rare) ; supprimer les anciens champs (l'historique perdrait les anciens montants) ; garder un montant total sur l'acompte (un total stocké, interdit).
- Conséquences : journal (D78) : les versements sont une onzième source, regroupée avec leur acompte ; le passage à vide des anciens champs n'est pas montré. Export : feuille « Versements d'acompte » (27 feuilles) et colonne « Total des versements » sur « Acomptes ». Import Excel : les versements comptent parmi les tables qui doivent être vides. Cas 21 à 24 de `regles-financieres/exemples.md`.

## D90 — Transfert de pièce entre véhicules
- Date : 2026-11-14 · Statut : adoptée (plan de l'étape 2.0 validé par Mathis : « toute la ligne ») ; précise D42
- Contexte : `PartTransfer` existe depuis la 1.2 sans écran. Changer le véhicule d'une ligne dans « Modifier la dépense » est une correction de saisie, sans trace (D42). Un véhicule abandonné sert de donneur de pièces, et une pièce montée ailleurs doit suivre avec son coût.
- Décision :
  - « Transférer sur un autre véhicule », sur la page de la dépense, sous chaque ligne posée sur un véhicule. Toute la ligne change de véhicule (pas une partie de sa quantité), avec une ligne `PartTransfer` (départ, arrivée, date, notes). Ses payeurs, ses remboursements et son statut ne changent pas : le coût suit la pièce, le solde entre associés ne bouge pas (cas 15). Aucune migration.
  - Le véhicule d'arrivée est choisi parmi ceux qu'une dépense propose (`vehicles_open_to_expenses` : au garage, ou vendu à la fiche rouverte), sauf celui où est la ligne.
  - Refus, dans le service et relus en base : la ligne d'achat d'un véhicule (D42, donc aussi l'achat d'un véhicule repris, D51) ; une ligne sur le garage (elle se met sur un véhicule dans « Modifier la dépense ») ; une pièce à commander ; un achat personnel ; une dépense à la corbeille ; une dépense verrouillée, même partagée avec un autre véhicule vendu (D50) ; un véhicule d'arrivée vendu non rouvert (D50), abandonné ou à la corbeille (D77), avec le même message que « Modifier la dépense » ; une ligne qui a changé de véhicule pendant que la page restait ouverte (« Recharge la page. »). Une ligne posée sur un véhicule à la corbeille peut en sortir (D77).
  - Le départ peut être un véhicule abandonné (donneur de pièces) : sa perte baisse du coût de la pièce, dans l'année de son abandon, comme pour toute dépense corrigée sur lui. Le bénéfice et l'argent encore dans le garage montent donc tous deux de ce coût (invariant 7) ; le solde et les positions ne changent pas.
  - Un transfert qui touche le véhicule repris d'une vente en cours reste permis ; il bloque ensuite l'annulation de la vente (D76), même si la pièce est repartie.
  - Trace : chaque ligne de la dépense dit « Transférée le … du … vers le … » ; la fiche de chaque véhicule a un bloc « Pièces transférées » (« reçue du », « donnée au »), sans les lignes ou dépenses à la corbeille. Le journal regroupe le transfert et le changement de véhicule de la ligne dans l'entrée de la dépense (un clic, une entrée).
- Alternatives écartées : transférer une partie de la quantité (une nouvelle ligne à couper, avec ses frais de port et ses parts : plus de code d'argent pour un cas rare) ; un écran de transfert sur la fiche véhicule (la dépense montre déjà le coût, les payeurs et les retours de la ligne) ; refuser un transfert qui touche un véhicule repris (il gênerait l'usage ordinaire de la reprise).
- Conséquences : `expenses.services` (`transfer_refusal`, `transfer_targets`, `transfer_line`, `vehicle_transfers`) ; `expenses.forms.TransferForm` ; vue `expenses:line_transfer`, absente de la consultation (D79). Export et import Excel avaient déjà la table.

## D91 — Liste des véhicules abandonnés
- Date : 2026-11-14 · Statut : adoptée (plan de l'étape 2.0 validé par Mathis : « lecture seule, avec leur perte »)
- Contexte : un véhicule abandonné n'apparaissait dans aucune liste ; sa perte n'était visible qu'au bilan de l'année de son abandon (D59).
- Décision :
  - Page « Véhicules abandonnés » (`/abandonnes/`), lien « Abandonnés » dans la barre latérale, sous « Vendus » (icône `circle-x`). Une carte par véhicule abandonné hors corbeille, l'abandon le plus récent d'abord : photo, date d'abandon, coût de revient, perte, acompte conservé déduit s'il y en a, perte de chaque associé. En tête : le nombre de véhicules et la perte de tous les abandons.
  - Les chiffres viennent du bilan (`queries.load_report`, D59 : coût de revient − acomptes conservés) : la page, « Bilan » et `controle_comptes` disent la même chose. La mise en forme passe par `accounting.services.abandoned_display` (moitié de chacun, sans signe). Un abandonné sans argent dit « Aucune perte » ; un acompte conservé plus grand que le coût donne « Gain de … ».
  - Lecture seule : aucune action sur la page ; on agit depuis la fiche (changer le statut, transférer une pièce, D90). Elle est dans `READ_ONLY_PAGES` (D79).
- Alternatives écartées : un filtre « Abandonnés » dans « Au garage » (un abandonné n'est plus au garage, et sa carte doit montrer une perte, pas un investissement) ; une tuile de plus sur l'accueil (rarement utile au quotidien).
- Conséquences : `accounting.queries` (`abandoned_vehicles`, `AbandonedCard`, `AbandonedList`) ; `accounting.services` (`abandoned_display`, `AbandonedDisplay`) ; vue `vehicles:abandoned`, gabarits `vehicles/abandoned_list.html` et `components/carte_vehicule_abandonne.html`. Aucune migration.

## D92 — Miniatures des photos, et plus de 100 photos d'un coup
- Date : 2026-11-15 · Statut : adoptée (plan de l'étape 2.0 validé par Mathis : « avec Pillow, stockées à part, refaites si elles manquent, hors de la sauvegarde ») ; précise D37 et D71
- Contexte : les cartes et la galerie chargeaient les photos d'origine, jusqu'à 20 Mo chacune. Au-delà de 100 photos envoyées d'un coup, Django répondait par une erreur 400 brute (`DATA_UPLOAD_MAX_NUMBER_FILES`).
- Décision :
  - Nouvelle dépendance : **Pillow** (`uv add pillow`, dépendance du logiciel et non outil de développement). Elle arrive chez Axel par `uv sync --locked --no-dev`, que l'installateur et « Mettre à jour » lancent déjà : aucun script ne change. Pillow n'est importé qu'au moment de faire une miniature : si « Mettre à jour » s'arrête après le `git pull` et avant la fin de `uv sync` (Internet coupé), le logiciel démarre encore, la sauvegarde et `controle_comptes` marchent, et les photos d'origine s'affichent (relecture du gardien, testé). Elle ne sert qu'aux miniatures ; la vérification des photos à l'envoi reste celle des premiers octets (D37).
  - Une miniature JPEG par photo, de 640 pixels au plus sur son côté le plus long, tournée comme sur le téléphone (EXIF), sur fond blanc si la photo avait de la transparence ; une petite photo n'est pas agrandie. Elle est rangée à part, dans `media/miniatures/`, avec le chemin de sa photo (`miniatures/vehicules/3/moto.jpg.jpg`). Rien dans la base : aucune migration.
  - Elle est faite à l'envoi, et refaite à la première demande si elle manque (après une restauration, ou pour les photos d'avant la 2.0). Adresse : `/photos/<n°>/miniature/?v=<nom du fichier>` (`core.views.photo_thumbnail`, filtre `thumbnail_url`), dans `READ_ONLY_PAGES` ; le nom du fichier dans l'adresse évite qu'après une restauration le navigateur montre, pour un même numéro, la miniature d'une autre photo. Une photo que Pillow ne sait pas réduire (abîmée, EXIF étrange, plus de 60 millions de pixels) est montrée telle quelle (renvoi vers la photo d'origine), l'erreur est notée dans le journal du serveur ; l'envoi ne la refuse pas pour autant. Chemins vérifiés par `safe_join`.
  - Les cartes (au garage, vendus, abandonnés) et la galerie montrent la miniature ; un clic dans la galerie ouvre la photo d'origine en grand.
  - La sauvegarde ne recopie pas `miniatures/` (`core.backup.mirror_media`) : ce ne sont que des copies réduites. En consultation (D79), rien n'est écrit : `MEDIA_PATH` est le dossier d'une sauvegarde, la miniature est faite en mémoire à chaque demande.
  - Plus de 100 photos d'un coup : l'écran arrête l'envoi avant de l'envoyer, avec « Tu as choisi plus de 100 photos : rien n'est envoyé. Envoie-les en plusieurs fois, 100 au plus à chaque envoi. » Si l'envoi part quand même, le refus de Django (`TooManyFilesSent`, levé dès la protection CSRF) est rattrapé par `core.views.bad_request` (`handler400`) : un message, et retour à la fiche. La limite reste celle de Django, écrite dans les réglages (100) et lue par l'écran ; le message parle de « fichiers », car il vaut aussi pour les factures. En développement (`DEBUG=1`, D26), Django montre sa page technique à la place : le message du serveur ne se voit qu'avec `DEBUG=0` (lanceur).
- Alternatives écartées : la miniature dans un champ de `Attachment` (une migration pour une donnée qui se refait) ; la sauvegarder (des centaines de fichiers de plus pour rien) ; relever la limite de 100 (un envoi trop gros resterait possible ; envoyer en plusieurs fois est simple) ; faire les miniatures seulement à la demande (la première visite de la galerie serait lente).
- Conséquences : `core/thumbnails.py` ; `vehicles.services.add_photos` appelle `thumbnails.prepare` ; le nom du dossier (`THUMBNAILS_FOLDER`) est dans les réglages, pour que la sauvegarde n'ait pas besoin de Pillow. La consultation ne garde pas les miniatures d'une fois sur l'autre (assumé : Mathis seul, rarement).

## D93 — Retour automatique quand les bibliothèques ne s'installent pas
- Date : 2026-11-15 · Statut : adoptée (décision de Mathis après l'essai de la sous-étape 4 de la 2.0) ; précise D86
- Contexte : si `uv sync` échouait après le `git pull` (Internet coupé pendant la mise à jour), le nouveau code restait en place avec les anciennes bibliothèques. Depuis D92, le logiciel démarre encore dans ce cas, mais sur un mélange de deux versions, jusqu'à une relance réussie.
- Décision :
  - Le code Git d'avant est noté avant le `git pull` ; s'il n'a pas la forme d'un code Git, la mise à jour s'arrête avant le pull. uv est cherché avant le pull, et non plus après.
  - Si `uv sync --locked --no-dev` échoue, « Mettre à jour » revient tout seul à ce code (`git reset --hard`, sans risque : aucun fichier suivi n'était modifié, c'est contrôlé avant le pull ; `donnees/` et `media/` ne sont pas suivis), remet les bibliothèques d'avant sans Internet (`uv sync --locked --no-dev --offline`, depuis le cache de uv), puis vérifie que le logiciel se charge (`manage.py check`).
  - La base n'est pas touchée : `migrate` n'a pas tourné et le repère `mise-a-jour-a-verifier` n'est pas posé. Rien n'est restauré.
  - Message : « Les bibliothèques de la nouvelle version n'ont pas pu être installées. MAx Engine est revenu à la version d'avant (1.0.0), sans rien perdre. Tu peux l'utiliser normalement : ouvre-le comme d'habitude avec l'icône MAx Engine. » puis de vérifier Internet et de relancer. Sans nouvelle version : « Les bibliothèques n'ont pas pu être vérifiées. MAx Engine n'a pas changé ». Si le retour lui-même échoue : « N'utilise pas MAx Engine : préviens Mathis. », la sauvegarde étant faite et la base intacte ; le journal donne alors à Mathis la ligne « Retour à faire à la main » (code et bibliothèques seulement, jamais la base ; README).
- Essais (copies dans le dossier temporaire : faux dépôt GitHub, installation clonée, copie de la base d'essai) : verrou des bibliothèques faux ; nouvelle bibliothèque absente du cache avec `UV_OFFLINE=1` ; relance avec Internet ; mise à jour ordinaire. Chaque fois : code revenu au commit d'avant, base identique octet pour octet, pas de repère, puis mise à jour réussie à la relance, comptes identiques. Contrôle de `gardien-des-donnees` (2026-11-15), avec de faux `git` et `uv` pour provoquer les pannes : sans nouvelle version ; `git reset` raté (le lanceur refuse alors d'ouvrir une base sans les migrations du code resté en place) ; `uv sync --offline` du retour raté ; code d'avant invalide ; fichiers non suivis et ignorés (`donnees/`, `media/`, `sauvegardes/`) intacts ; rotation des sauvegardes « mise-a-jour » sans perte. Rien de bloquant ; ses deux corrections (message sans nouvelle version, marche à suivre d'un retour raté) sont faites.
  - Limite connue : PowerShell lit le script avant le `git pull`, donc une mise à jour tourne avec le script de la version installée. Sans conséquence ici : la 2.0.0 est installée directement chez Axel par l'installateur.
- Alternatives écartées : laisser le nouveau code en place (D92 le rend utilisable, mais deux versions mélangées) ; revenir en arrière aussi après `migrate` (il faudrait restaurer la base : c'est à Mathis de juger, README).
- Conséquences : `scripts/mettre_a_jour.ps1` (fonction `Revenir-Avant`) ; tests de l'ordre des étapes dans `core/tests/test_install_scripts.py`.

## D94 — L'import se contrôle contre le Bilan de l'Excel lui-même
- Date : 2026-11-18 · Statut : adoptée (plan de la tâche 2.0.1 validé par Mathis) ; modifie D82
- Contexte : à la répétition de l'installation, `importer_excel` a refusé le vrai fichier (`donnees/garage.xlsx`), qui a changé depuis la 1.7 : 27 dépenses au lieu de 18, « Axel doit 312,45 € » au lieu de 205,44 €. Les comptes importés étaient justes (égaux au Bilan de l'Excel, recalculés aussi à partir des lignes) ; c'étaient les chiffres écrits dans le code (`EXPECTED_…`) qui dataient. Les écarts du solde et des coûts s'affichaient en listes brutes, avec des numéros internes.
- Décision :
  - Plus aucune valeur de contrôle dans le code. L'import lit « Bilan & Comptes » : la ligne de chaque moto (« Coût de Revient Total », « Investissement Mathis », « Investissement Axel »), la ligne « TOTAL GLOBAL » (investissements, « Investissement global ») et la phrase « Egalisation » (« Axel doit 312,45 € à Mathis », relue en débiteur, créancier, montant). Les cellules sont retrouvées par leur titre de colonne et leur nom de ligne. Il refuse toujours au moindre écart, et aussi si une cellule manque ou n'a pas de valeur enregistrée par Excel (formule jamais calculée) : l'import ne refait pas le calcul d'Excel.
  - Chaque écart est une phrase : ce que donne MAx Engine, ce que dit l'Excel, et la cellule (« Bilan & Comptes, ligne 9 »). Les nombres de véhicules, dépenses et lignes ne sont plus comparés (l'Excel ne les donne pas), seulement affichés.
  - Moto sans part payée : une moto nommée dans le registre, sans prix ni part, n'est pas importée ; le compte rendu la liste « à saisir à la main » et l'import continue. Une ligne du journal qui la cite fait refuser l'import. Une moto avec un prix mais sans part est refusée. Une ligne du Bilan d'une moto non importée n'est acceptée qu'à 0 (avertissement si la moto est absente du registre).
  - Plausibilité, décidée par Mathis : une date dans le futur (achat ou journal), ou une ligne du journal datée de plus d'un an avant l'achat de sa moto, fait refuser l'import, toutes les lignes en cause listées. Des frais de port négatifs et deux lignes identiques le même jour (même moto, désignation et référence) sont des avertissements du compte rendu, sans bloquer ; ils sont redits avec un refus pour écart.
  - Après relecture : une ligne du journal sans moto est refusée dès la lecture (le Bilan ne la compte pas : l'écart des totaux serait inexplicable) ; elle se saisit dans MAx Engine après l'import. Une cellule du Bilan en erreur (`#REF!`) a son propre message ; les lignes sous « TOTAL GLOBAL » ne sont pas lues. Pas de tolérance d'un centime sur le solde (D33) : non demandé, un écart d'un centime refuse.
- Pourquoi : l'Excel continue de vivre jusqu'à l'installation ; des chiffres écrits dans le code refuseraient à chaque changement un fichier juste. Le Bilan de l'Excel est ce qu'Axel et Mathis regardent : l'import doit retomber exactement dessus. L'erreur « ligne 5 : aucune part payée » venait d'une version du fichier où la « 125 KLX » était notée au registre avant d'être payée.
- Alternatives écartées : recalculer les totaux à partir des lignes dans l'import (il se contrôlerait lui-même) ; refaire les formules d'Excel quand la valeur manque (une bibliothèque de plus, et un calcul qui pourrait différer d'Excel) ; ignorer la ligne « Inclus » d'une moto mise de côté (de l'argent disparaîtrait sans le dire).
- Conséquences : `core/excel_import.py`, `importer_excel` (avertissements et motos à saisir à la main dans le compte rendu) ; la copie anonymisée des tests porte une feuille « Bilan & Comptes » ; `docs/import-excel.md` (« Plausibilité », « Contrôle après import ») ; répétition de l'installation sur le nouveau fichier (README).
