# SPEC-TOL — Tolérance des écarts de valorisation

Version 1.2 · Banque fictive, document de démonstration · Responsable : équipe Validation des modèles

## Contexte

Une campagne de non-régression valorise le même portefeuille avec la version
de référence de la librairie de valorisation et avec la version candidate.
Pour chaque opération, l'écart de PV est la valeur candidate moins la valeur
de référence, en EUR. Cette spécification dit quand un écart est accepté et
quand il doit être signalé à l'équipe de validation.

Chaque règle porte un identifiant stable. Une ligne « Borne » donne la
condition sous une forme que le code sait lire ; le texte de la règle fait foi.

## R-01 — Écart toléré sur un swap de taux

Pour un swap de taux (code produit IRS), un écart de PV est accepté si sa
valeur absolue est inférieure ou égale à 50,00 EUR. Au-delà, l'écart est
signalé. Un écart négatif se juge comme un écart positif de même valeur absolue.

Borne : `|ecart_pv| <= 50.00 EUR` · pas 0.01 · si vrai « accepté » · sinon « signalé »

## R-02 — Écart toléré sur une obligation d'État

Pour une obligation d'État (code produit BOND), un écart de PV est accepté si
sa valeur absolue est inférieure ou égale à 25,00 EUR. Au-delà, il est signalé.

Borne : `|ecart_pv| <= 25.00 EUR` · pas 0.01 · si vrai « accepté » · sinon « signalé »

## R-03 — Écart toléré sur une option de change

Pour une option de change vanille (code produit FXO), un écart de PV est
accepté si sa valeur absolue est inférieure ou égale à 120,00 EUR. Au-delà, il
est signalé.

Borne : `|ecart_pv| <= 120.00 EUR` · pas 0.01 · si vrai « accepté » · sinon « signalé »

## R-04 — Bruit numérique

Un écart signalé est classé « bruit » quand son rapport à la tolérance de son
produit est strictement inférieur à 5. À partir de 5 fois la tolérance, il est
classé « significatif » et doit être expliqué par une note de version ou un
ticket. Le rapport se calcule au centième.

Borne : `rapport_tolerance < 5` · pas 0.01 · si vrai « bruit » · sinon « significatif »

## R-05 — Produit sans tolérance

Un produit qui n'a pas de tolérance définie dans le référentiel ne bénéficie
d'aucune tolérance : tout écart, même nul en apparence, est signalé, avec la
mention « tolérance absente ».

## R-06 — Valorisation en échec

Quand la version candidate ou la version de référence ne produit pas de valeur
(valorisation en échec), il n'y a pas d'écart à mesurer. Le résultat est
toujours signalé, avec la mention « valorisation en échec », et ne peut jamais
être classé comme bruit.
