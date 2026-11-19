"""Les 18 dépenses du jeu de référence (cas 5 des règles financières).

Version publique : un jeu inventé, de même forme que le fichier Excel qui a servi de
contrôle au garage (3 achats de véhicule + 15 paiements de pièces et de sous-traitance,
22 lignes de pièces, 5 commandes groupées avec frais de port, un lot de deux roues sans
frais de port, un retour « à préciser »). Montants, motos et n° de commande sont fictifs.
La clé d'une dépense groupée est son n° de commande.

Véhicules : 1 = Yamaha YZ250, 2 = GasGas MC250, 3 = Sherco 80.
"""

from decimal import Decimal as D

from accounting.services import ExpenseInput, LineInput, Payment, RefundInput

M, A = "Mathis", "Axel"
PARTNERS = (M, A)
YAMAHA, GG, SHERCO = 1, 2, 3


def paid_by(*payments):
    return tuple(Payment(associe, D(montant)) for associe, montant in payments)


def line(vehicle, unit_price, quantity="1", refunds=()):
    return LineInput(D(quantity), D(unit_price), vehicle, refunds=refunds)


EXPENSES = (
    # Achats des véhicules (catégorie « Achat véhicule »)
    ExpenseInput(D("560.00"), paid_by((M, "280"), (A, "280")), (line(YAMAHA, "560"),)),
    ExpenseInput(D("180.00"), paid_by((A, "180")), (line(GG, "180"),)),
    ExpenseInput(D("440.00"), paid_by((M, "220"), (A, "220")), (line(SHERCO, "440"),)),
    # Yamaha YZ250
    ExpenseInput(
        D("86.00"),
        paid_by((M, "86")),
        (line(YAMAHA, "71.90"), line(YAMAHA, "8.40")),
        key="50017",
    ),
    ExpenseInput(
        D("210.40"),
        paid_by((M, "210.40")),
        (line(YAMAHA, "120"), line(YAMAHA, "70")),
        key="50021",
    ),
    # GasGas MC250
    ExpenseInput(D("180.00"), paid_by((A, "180")), (line(GG, "90"), line(GG, "90"))),
    ExpenseInput(D("88.50"), paid_by((M, "88.50")), (line(GG, "88.50"),)),
    ExpenseInput(D("64.90"), paid_by((M, "64.90")), (line(GG, "64.90"),)),
    ExpenseInput(D("132.75"), paid_by((M, "132.75")), (line(GG, "132.75"),)),
    ExpenseInput(D("120.00"), paid_by((M, "120")), (line(GG, "120"),)),  # sous-traitance
    ExpenseInput(D("38.60"), paid_by((A, "38.60")), (line(GG, "38.60"),)),
    ExpenseInput(
        D("24.80"),
        paid_by((A, "24.80")),
        (
            line(GG, "11.40"),
            # Pièce retournée : remboursement « à préciser », 0,00 €.
            line(GG, "4.10", refunds=(RefundInput(A, D("0.00")),)),
        ),
        key="C2024117",
    ),
    ExpenseInput(D("12.45"), paid_by((A, "12.45")), (line(GG, "12.45"),)),
    ExpenseInput(
        D("81.60"),
        paid_by((M, "81.60")),
        (line(GG, "37.20"), line(GG, "33.90")),
        key="CMD50019",
    ),
    ExpenseInput(
        D("48.20"),
        paid_by((A, "48.20")),
        (line(GG, "6.40", quantity="2"), line(GG, "19.90"), line(GG, "3.10", quantity="2")),
        key="CMD50018",
    ),
    ExpenseInput(D("16.92"), paid_by((A, "16.92")), (line(GG, "16.92"),)),
    ExpenseInput(D("152.30"), paid_by((A, "152.30")), (line(GG, "152.30"),)),
    ExpenseInput(D("280.00"), paid_by((M, "280")), (line(GG, "280"),)),  # avance au motoriste
)

# Frais de port de chaque commande groupée
EXPECTED_SHIPPING = {
    "50017": D("5.70"),
    "50021": D("20.40"),
    "C2024117": D("9.30"),
    "CMD50019": D("10.50"),
    "CMD50018": D("9.30"),
}

# Coût de revient : total, part de Mathis, part d'Axel
EXPECTED_COSTS = {
    YAMAHA: (D("856.40"), D("576.40"), D("280.00")),
    GG: (D("1421.02"), D("767.75"), D("653.27")),
    SHERCO: (D("440.00"), D("220.00"), D("220.00")),
}
