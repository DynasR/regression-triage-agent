# SPEC-INC — Format d'un ticket d'incident de valorisation

Version 1.0 · Banque fictive, document de démonstration · Responsable : support production

## Contexte

Un écart de valorisation qui n'est ni attendu ni connu donne lieu à un ticket
d'incident. Cette spécification fixe le format qu'un ticket doit respecter
pour être accepté par l'outil de suivi. Un ticket qui ne respecte pas une
règle est refusé avec le motif de la règle.

Chaque règle porte un identifiant stable. Une ligne « Borne » donne la
condition sous une forme que le code sait lire ; le texte de la règle fait foi.

## R-01 — Identifiant

L'identifiant d'un ticket est « INC- » suivi d'exactement six chiffres.
Exemple : INC-004217 est accepté ; INC-4217 et INC-0004217 sont refusés.

## R-02 — Longueur maximale du titre

Le titre d'un ticket compte au plus 80 caractères, espaces compris. Un titre
plus long est refusé.

Borne : `longueur_titre <= 80` · pas 1 · si vrai « accepté » · sinon « refusé »

## R-03 — Longueur minimale du titre

Le titre d'un ticket compte au moins 10 caractères, espaces compris. Un titre
plus court est refusé.

Borne : `longueur_titre >= 10` · pas 1 · si vrai « accepté » · sinon « refusé »

## R-04 — Priorité

La priorité vaut P1, P2 ou P3. Toute autre valeur, par exemple P0 ou P4, est
refusée. Une priorité absente est refusée.

## R-05 — Produit concerné

Le ticket nomme le code du produit concerné, parmi IRS, BOND, FXO, EQO et CDS.
Un code inconnu ou absent est refusé.
