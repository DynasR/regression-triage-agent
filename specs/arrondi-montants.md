# SPEC-ARR — Arrondi des montants et des taux

Version 1.1 · Banque fictive, document de démonstration · Responsable : équipe Référentiel

## Contexte

Les montants et les taux publiés par la librairie de valorisation sont
arrondis selon des règles fixes, pour que deux systèmes qui reçoivent la même
valeur affichent le même chiffre.

Chaque règle porte un identifiant stable. Une ligne « Arrondi » donne la règle
sous une forme que le code sait lire ; le texte de la règle fait foi.

## R-01 — Montants en EUR

Un montant en EUR est arrondi au centime, soit 2 décimales. Une valeur
exactement à mi-chemin s'arrondit en s'éloignant de zéro (demi vers le haut) :
2,345 devient 2,35 et -2,345 devient -2,35.

Arrondi : `arrondi(montant, 2, demi-haut)`

## R-02 — Taux

Un taux est arrondi à 4 décimales. Une valeur exactement à mi-chemin
s'arrondit vers le chiffre pair le plus proche (arrondi bancaire) : 0,03125
devient 0,0312 et 0,03135 devient 0,0314.

Arrondi : `arrondi(taux, 4, demi-pair)`

## R-03 — Un seul arrondi

L'arrondi s'applique une seule fois, au résultat final. Les calculs
intermédiaires gardent toute leur précision : arrondir une étape
intermédiaire est une anomalie.

## R-04 — Valeur absente

Un montant ou un taux absent n'est pas remplacé par zéro : il reste absent,
et le résultat qui en dépend est marqué « non calculable ».
