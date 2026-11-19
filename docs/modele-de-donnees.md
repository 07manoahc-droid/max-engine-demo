# Modèle de données — MAx Engine

Source de vérité du schéma. Toute évolution du modèle commence par une mise à jour de ce document, puis une migration.

Conventions communes à toutes les tables :

- Clé primaire `id` automatique (`BigAutoField`).
- `created_at`, `updated_at` (horodatage automatique) sur toutes les tables métier.
- `deleted_at` (date et heure, vide par défaut) sur les tables où l'utilisateur supprime des éléments : suppression logique, restaurable depuis la corbeille. Le gestionnaire par défaut (`objects`) exclut les éléments supprimés ; `all_objects` les inclut.
- Historique (django-simple-history, via `core.history.partner_history()` : l'auteur est un associé, voir D30) sur toutes les tables qui portent de l'argent ou un statut : `Vehicle`, `Expense`, `ExpenseLine`, `ExpensePayment`, `Refund`, `PartTransfer`, `Sale`, `SaleReceipt`, `Deposit`, `DepositReceipt`, `Settlement`.
- Montants : `DecimalField(max_digits=10, decimal_places=2)`, en euros TTC. Quantités : `DecimalField(max_digits=8, decimal_places=2)`.
- `verbose_name` en français sur chaque modèle et chaque champ (ce sont les libellés de l'interface).

## Vue d'ensemble

```text
Partner ──< ExpensePayment >── Expense ──< ExpenseLine >── Vehicle
Partner ──< SaleReceipt >── Sale >── Vehicle (une seule vente non annulée par véhicule)
Partner ──< DepositReceipt >── Deposit >── Vehicle
Partner ──< Settlement (de / vers)
ExpenseLine ──< Refund
ExpenseLine ──< PartTransfer
Vehicle ──< WorkLog, Task, ComparableListing, StatusChange, Attachment
Contact ──< Vehicle (vendeur), Sale (client), Deposit (client)
```

Une ligne de dépense sans véhicule est une dépense générale du garage (outillage, consommables).

## core

### Partner — associé

| Champ | Type | Règle |
| --- | --- | --- |
| `name` | texte 50 | unique. Valeurs initiales : Mathis, Axel |
| `color` | texte 20 | clé de couleur de la charte (`indigo`, `teal`) |
| `is_active` | booléen | vrai par défaut |

Le logiciel suppose exactement deux associés actifs pour le partage à parts égales. Une donnée initiale (migration de données) crée les deux associés.

### GarageSettings — paramètres (une seule ligne)

Toujours la ligne 1 (contrainte en base), créée par migration ; lue par `GarageSettings.load()`.

| Champ | Type | Règle |
| --- | --- | --- |
| `garage_name` | texte 80 | « MAx Engine » par défaut |
| `backup_dir` | texte 255 | dossier OneDrive des sauvegardes |
| `stale_sale_days` | entier | seuil d'alerte « en vente depuis trop longtemps » (étape 3), 60 par défaut |

### VehicleType, PurchaseOrigin — listes modifiables

| Champ | Type | Règle |
| --- | --- | --- |
| `name` | texte 50 | unique |
| `order` | entier | ordre d'affichage |
| `is_active` | booléen | une valeur utilisée n'est jamais supprimée, seulement désactivée |

Valeurs initiales — types : Moto, Scooter, Quad. Origines : Leboncoin, Connaissance, Autre.

### ExpenseCategory — catégorie de dépense

| Champ | Type | Règle |
| --- | --- | --- |
| `name` | texte 50 | unique |
| `code` | texte 30 | unique, stable, utilisé par le code |
| `is_system` | booléen | une catégorie système ne peut être ni renommée dans son code, ni désactivée |
| `order`, `is_active` | | comme les listes ci-dessus |

Valeurs initiales (code : nom) : `vehicle_purchase` Achat véhicule (système), `parts` Pièces, `subcontracting` Sous-traitance, `tooling` Outillage, `consumable` Consommable, `other` Autre.

### Supplier — fournisseur

| Champ | Type | Règle |
| --- | --- | --- |
| `name` | texte 100 | unique, insensible à la casse ; proposé en autocomplétion |
| `website` | URL | facultatif |
| `notes` | texte long | facultatif |

### Contact — vendeur ou client

| Champ | Type | Règle |
| --- | --- | --- |
| `name` | texte 100 | obligatoire |
| `phone`, `email`, `city` | texte | facultatifs |
| `leboncoin_profile` | URL | facultatif |
| `notes` | texte long | facultatif |
| `deleted_at` | date | suppression logique |

Une même personne peut être vendeur et client. La fiche affiche les véhicules liés.

### Attachment — fichier joint (photo, facture, document)

| Champ | Type | Règle |
| --- | --- | --- |
| `file` | fichier | rangé dans `media/vehicules/<numéro>/` ou `media/garage/` |
| `kind` | choix | `PHOTO`, `INVOICE` (facture), `DOCUMENT` |
| `stage` | choix | pour les photos : `LISTING` annonce, `BEFORE` avant, `DURING` pendant, `AFTER` après |
| `caption` | texte 200 | facultatif |
| `show_to_buyer` | booléen | facture à inclure dans le dossier acheteur |
| `vehicle` | FK Vehicle | facultatif |
| `expense` | FK Expense | facultatif (facture d'un paiement) |
| `work_log` | FK WorkLog | facultatif (facture d'une intervention) |
| `order` | entier | ordre dans la galerie |
| `deleted_at` | date | suppression logique |

Au moins un rattachement (`vehicle`, `expense` ou `work_log`) est obligatoire.

Une facture (`INVOICE`) est rattachée à sa dépense ; si toutes les lignes de la dépense visent le même véhicule, elle l'est aussi à ce véhicule, ce qui la range dans son dossier (D42).

## vehicles

### Vehicle — véhicule

| Champ | Type | Règle |
| --- | --- | --- |
| `number` | entier | unique, attribué automatiquement (max + 1, véhicules supprimés compris), jamais réutilisé ni modifiable |
| `vehicle_type` | FK VehicleType | obligatoire |
| `brand`, `model` | texte 60 | obligatoires |
| `year` | entier | facultatif |
| `displacement_cc` | entier | cylindrée, facultatif |
| `vin` | texte 17 | facultatif, majuscules |
| `registration` | texte 15 | immatriculation, facultatif |
| `mileage_km` | entier | facultatif |
| `color` | texte 30 | facultatif |
| `status` | choix `Status` | voir ci-dessous |
| `origin` | FK PurchaseOrigin | facultatif |
| `seller` | FK Contact | facultatif |
| `listing_url` | URL | annonce d'origine |
| `asking_price` | montant | prix demandé par le vendeur (en attente d'achat) |
| `location` | texte 100 | lieu |
| `purchased_on` | date | renseignée au passage en « Acheté » |
| `sale_price_target` | montant | prix de vente affiché (en vente) |
| `listed_on` | date | date de mise en vente |
| `notes` | texte long | |
| `reopened_at` | date | « Rouvrir la fiche » d'un véhicule vendu : vide = dépenses verrouillées, renseignée = déverrouillées (voir plus bas) |
| `reopen_reason` | texte long | motif de la réouverture, obligatoire quand `reopened_at` est renseignée |
| `deleted_at` | date | suppression logique |

Nom affiché (`display_name`) : `n°{number} · {brand} {model} {year} · {vin}`, les parties vides étant omises avec leur séparateur.

Le prix d'achat n'est pas un champ du véhicule : c'est une ligne de dépense de catégorie `vehicle_purchase`. Ainsi tous les coûts et tous les payeurs passent par le même mécanisme.

#### Status — statuts

`WAITING_PURCHASE` En attente d'achat → `PURCHASED` Acheté → `IN_REPAIR` En réparation → `READY_FOR_SALE` Prêt à vendre → `FOR_SALE` En vente → `RESERVED` Réservé → `SOLD` Vendu. Sortie possible à tout moment avant la vente : `ABANDONED` Abandonné.

Règles de transition :

- Passer en `PURCHASED` exige une ligne `vehicle_purchase` payée (le formulaire d'achat la crée).
- Passer en `RESERVED` exige un acompte actif, et se fait depuis `PURCHASED`, `IN_REPAIR`, `READY_FOR_SALE` ou `FOR_SALE` ; annuler l'acompte ramène au statut d'avant la réservation, lu dans `StatusChange` (`FOR_SALE` s'il n'est pas retrouvé) (D47).
- Passer en `SOLD` exige une vente (`Sale`) complète, depuis `PURCHASED`, `IN_REPAIR`, `READY_FOR_SALE`, `FOR_SALE` ou `RESERVED` ; annuler la vente ramène en `FOR_SALE` (D49).
- Revenir en arrière est permis sauf depuis `SOLD` (il faut annuler la vente) et sauf vers `WAITING_PURCHASE` une fois l'achat enregistré (D35) ; chaque changement crée un `StatusChange`, y compris la création du véhicule (`from_status` vide).
- Passer en `FOR_SALE` renseigne `listed_on` s'il est vide.

Fiche d'un véhicule vendu :

- Les dépenses d'un véhicule au statut `SOLD` sont **verrouillées** : lignes, paiements et retours ne se modifient plus.
- L'action **« Rouvrir la fiche »** les déverrouille : elle exige un motif (`reopen_reason`) et renseigne `reopened_at`. Elle **ne change pas le statut** (le véhicule reste `SOLD`, donc dans « Vendus » et dans le bilan) et ne crée pas de `StatusChange`. `Vehicle` a un historique : la réouverture et son motif y sont tracés.
- Le bouton **« Verrouiller la fiche »** remet `reopened_at` à vide.
- Marges, coûts de revient et soldes se calculent toujours sur les **ventes non annulées**, jamais sur le statut ni sur `reopened_at`.

### StatusChange — historique des statuts

| Champ | Type |
| --- | --- |
| `vehicle` | FK Vehicle |
| `from_status`, `to_status` | choix `Status` (`from_status` vide à la création) |
| `changed_at` | date et heure |
| `changed_by` | FK Partner (facultatif : vide pour un changement fait par une commande) |

Sert à la frise de la fiche véhicule et aux durées (achat → vente).

### WorkLog — intervention

| Champ | Type | Règle |
| --- | --- | --- |
| `vehicle` | FK Vehicle | obligatoire |
| `date` | date | |
| `description` | texte long | |
| `kind` | choix | `REPAIR` réparation, `MAINTENANCE` entretien, `UPGRADE` amélioration |
| `done_by` | FK Partner | facultatif ; vide = sous-traité |
| `hours` | décimal | facultatif, informatif, n'entre dans aucun calcul |
| `deleted_at` | date | |

Une intervention sans facture apparaît comme « réalisée par nos soins » dans le dossier acheteur.

### Task — chose à faire

| Champ | Type |
| --- | --- |
| `vehicle` | FK Vehicle |
| `label` | texte 200 |
| `is_done` | booléen |
| `done_at` | date |
| `order` | entier |
| `deleted_at` | date et heure |

### ComparableListing — annonce comparable (étape 2)

| Champ | Type |
| --- | --- |
| `vehicle` | FK Vehicle |
| `url` | URL |
| `price` | montant |
| `year`, `mileage_km` | entiers, facultatifs |
| `notes` | texte |
| `noted_on` | date |
| `deleted_at` | date et heure |

## expenses

### Expense — dépense (un paiement)

| Champ | Type | Règle |
| --- | --- | --- |
| `date` | date | obligatoire |
| `supplier` | FK Supplier | facultatif |
| `order_number` | texte 60 | facultatif |
| `total_paid` | montant | montant réellement payé, frais de port compris ; > 0 |
| `is_personal` | booléen | achat personnel : gardé pour mémoire, exclu de tous les calculs |
| `notes` | texte long | |
| `deleted_at` | date | |

Contraintes :

- La somme des `ExpensePayment.amount` égale `total_paid`.
- Une dépense a au moins une ligne.
- Une dépense liée à un véhicule vendu (`SOLD`) est verrouillée tant que la fiche n'est pas rouverte (`Vehicle.reopened_at`, tracé dans l'historique). Si ses lignes touchent plusieurs véhicules, elle est verrouillée dès que l'un d'eux est vendu et non rouvert.

### ExpensePayment — part payée par un associé

| Champ | Type | Règle |
| --- | --- | --- |
| `expense` | FK Expense | |
| `partner` | FK Partner | |
| `amount` | montant | > 0 |
| `deleted_at` | date et heure | suppression logique : payeur retiré d'une dépense modifiée (D40) |

Unicité (`expense`, `partner`) parmi les paiements hors corbeille. Les paiements à la corbeille ne comptent dans aucun calcul. Le formulaire propose « Mathis », « Axel », « Moitié-moitié » ou « Montants différents ».

### ExpenseLine — ligne de dépense

| Champ | Type | Règle |
| --- | --- | --- |
| `expense` | FK Expense | vide tant que la pièce est « À commander » |
| `vehicle` | FK Vehicle | vide = dépense générale du garage |
| `category` | FK ExpenseCategory | obligatoire |
| `label` | texte 200 | désignation |
| `reference` | texte 80 | référence de la pièce |
| `quantity` | quantité | 1 par défaut, > 0 |
| `unit_price` | montant | prix unitaire TTC, ≥ 0 |
| `status` | choix | pièces : `TO_ORDER` À commander, `ORDERED` Commandé, `RECEIVED` Reçu, `FITTED` Monté, `RETURNED` Retourné/Remboursé ; autres catégories : vide |
| `show_to_buyer` | booléen | apparaît dans le dossier acheteur |
| `deleted_at` | date | |

Calculs (dans `accounting/services.py`, jamais stockés) :

- `line_total = quantity × unit_price`, arrondi au centime (`ROUND_HALF_UP`) si la quantité a des décimales
- `allocated_total` = `line_total` + part des frais de port (voir `regles-financieres`)
- coût net = `allocated_total` − remboursements

Une ligne `TO_ORDER` sans dépense n'entre dans aucun calcul. Contrainte en base : une ligne sans dépense est forcément `TO_ORDER`.

### Refund — remboursement (retour)

| Champ | Type | Règle |
| --- | --- | --- |
| `line` | FK ExpenseLine | |
| `date` | date | |
| `amount` | montant | ≥ 0 ; la somme des remboursements d'une ligne ≤ son total réparti ; 0,00 = montant « à préciser » |
| `refunded_to` | FK Partner | proposé : le payeur s'il est seul ; avec deux payeurs, rien n'est choisi d'avance |
| `notes` | texte | |
| `deleted_at` | date et heure | suppression logique |

### PartTransfer — transfert de pièce

| Champ | Type |
| --- | --- |
| `line` | FK ExpenseLine |
| `from_vehicle`, `to_vehicle` | FK Vehicle |
| `date` | date |
| `notes` | texte |

Le transfert met à jour `ExpenseLine.vehicle` et garde la trace. Le payeur ne change pas. Toute la ligne est transférée ; règles et refus : D90.

## sales

### Sale — vente

| Champ | Type | Règle |
| --- | --- | --- |
| `vehicle` | FK Vehicle | un véhicule peut avoir plusieurs ventes au fil du temps (vente annulée puis revente), mais une seule non annulée |
| `date` | date | |
| `price` | montant | prix total, reprise comprise |
| `buyer` | FK Contact | facultatif |
| `sold_where` | texte 200 | où et comment (texte libre) |
| `trade_in_vehicle` | 1-1 Vehicle | facultatif : véhicule repris |
| `trade_in_value` | montant | 0 par défaut |
| `notes` | texte | |
| `cancelled_at` | date | annulation (le véhicule revient en vente, D49) |

Une vente non annulée se modifie (prix, date, acheteur, lieu, notes, encaissements), sauf sa reprise (D49).

Contraintes :

- `UniqueConstraint(fields=["vehicle"], condition=Q(cancelled_at__isnull=True))` : une seule vente non annulée par véhicule. `Sale` n'a pas de `deleted_at` (l'annulation passe par `cancelled_at`) ; si un `deleted_at` est ajouté un jour, ajouter `deleted_at__isnull=True` à la condition.
- Somme des encaissements (`SaleReceipt`) + versements des acomptes appliqués + `trade_in_value` = `price` (vérifiée par les services et `controle_comptes`).
- En base : `price` > 0, 0 ≤ `trade_in_value` ≤ `price`, le véhicule repris est un autre véhicule.

### SaleReceipt — encaissement

| Champ | Type |
| --- | --- |
| `sale` | FK Sale |
| `partner` | FK Partner (qui a reçu l'argent) |
| `amount` | montant > 0 |
| `date` | date |
| `deleted_at` | suppression logique : encaissement retiré en modifiant la vente (D49) |

Un encaissement à la corbeille ne compte nulle part : le gestionnaire par défaut l'écarte. Il n'y a pas d'unicité (`sale`, `partner`) : le modèle permet qu'un associé encaisse en plusieurs fois, même si le formulaire de vente ne donne qu'un montant par associé.

### Deposit — acompte

| Champ | Type | Règle |
| --- | --- | --- |
| `vehicle` | FK Vehicle | |
| `buyer` | FK Contact | facultatif |
| `date` | date | date de la réservation |
| `status` | choix | `ACTIVE` en cours, `APPLIED` déduit d'une vente, `REFUNDED` rendu, `KEPT` conservé (réservation ou vente annulée) |
| `sale` | FK Sale | renseigné quand `APPLIED` |
| `legacy_amount` | montant | vide depuis la 2.0.0 (D89) |
| `legacy_received_by` | FK Partner | vide depuis la 2.0.0 (D89) |

L'argent de l'acompte est dans ses versements (`DepositReceipt`) : son montant est leur somme, jamais stocké (D89). `legacy_amount` et `legacy_received_by` portaient l'acompte avant la 2.0.0 ; la migration `sales.0005` les a recopiés en un versement puis vidés. Ils restent pour que l'historique garde les anciennes valeurs (D47), et rien ne les lit pour compter.

L'état vaut pour tous les versements de l'acompte. Un acompte `REFUNDED` n'a aucun effet financier. Un acompte `KEPT` est un encaissement du véhicule (il s'ajoute au prix de vente dans la marge, D32), chaque versement restant chez celui qui l'a reçu. Contraintes en base : un acompte `APPLIED` a une vente ; un véhicule a au plus un acompte `ACTIVE` (D47) ; `legacy_amount` et `legacy_received_by` sont vides ensemble, ou remplis ensemble avec un montant > 0.

### DepositReceipt — versement d'acompte

| Champ | Type |
| --- | --- |
| `deposit` | FK Deposit |
| `partner` | FK Partner (qui a reçu l'argent) |
| `amount` | montant > 0 |
| `date` | date |
| `deleted_at` | suppression logique : versement retiré en corrigeant l'acompte (D89) |

Un acompte a au moins un versement hors corbeille (vérifié par `controle_comptes`). Un versement à la corbeille ne compte nulle part et ne se restaure pas depuis « Corbeille » : on le ressaisit. Les versements ne se modifient que tant que l'acompte est en cours.

### TradeIn — reprise

Pas de table dédiée : la reprise est portée par `Sale.trade_in_vehicle` et `Sale.trade_in_value`. La validation de la vente crée automatiquement, pour le véhicule repris : la fiche au statut `PURCHASED` avec l'acheteur comme vendeur, et une dépense `vehicle_purchase` de `trade_in_value` payée moitié par chaque associé (centime impair au premier, comme dans les positions). La reprise est ainsi neutre pour le solde entre associés.

Tant que la vente n'est pas annulée, cette dépense garde son montant, ses payeurs et sa ligne unique. Annuler la vente met la dépense et la fiche reprise à la corbeille ; `Sale.trade_in_vehicle` garde le lien pour l'affichage de la vente annulée (D51).

## accounting

### Settlement — règlement entre associés

| Champ | Type | Règle |
| --- | --- | --- |
| `date` | date | |
| `from_partner`, `to_partner` | FK Partner | différents |
| `amount` | montant | > 0 |
| `notes` | texte | |
| `cancels` | 1-1 Settlement | renseigné pour un règlement d'annulation ; un règlement ne s'annule qu'une fois |

Jamais modifié ni supprimé : une erreur s'annule par un règlement inverse (`cancels`). Le modèle refuse toute modification et toute suppression ; une annulation reprend le même montant dans l'autre sens, et ne peut pas être annulée à son tour. Dans l'administration de secours, les règlements sont en lecture seule.

Le solde, les coûts, les marges et le bilan sont toujours recalculés à partir des tables ci-dessus. Aucun total n'est stocké.
