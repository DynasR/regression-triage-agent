# SPEC-BLQ — Blocage d'une livraison de la librairie de valorisation

Version 2.0 · Banque fictive, document de démonstration · Responsable : comité de mise en production

## Contexte

Après une campagne de non-régression, chaque écart reçoit des points de risque.
Le score de risque d'une livraison est la somme de ces points, un nombre
entier positif ou nul. Cette spécification dit, à partir du score et de
quelques faits, si la livraison part, attend un relecteur ou est bloquée.

Chaque règle porte un identifiant stable. Une ligne « Borne » donne la
condition sous une forme que le code sait lire ; le texte de la règle fait foi.

## R-01 — Blocage par le score

Une livraison dont le score de risque est supérieur ou égal à 10 est bloquée.
En dessous de 10, elle n'est pas bloquée par cette règle.

Borne : `score_risque >= 10` · pas 1 · si vrai « bloquée » · sinon « non bloquée »

## R-02 — Revue humaine

Une livraison dont le score de risque est strictement inférieur à 3 part sans
relecture (livraison automatique). À partir d'un score de 3, une revue humaine
est requise, sauf si R-01 la bloque déjà.

Borne : `score_risque < 3` · pas 1 · si vrai « livraison automatique » · sinon « revue humaine »

## R-03 — Valorisation en échec

Une seule valorisation en échec dans la campagne bloque la livraison, quel que
soit le score de risque.

## R-04 — Délai du relecteur

Quand une revue humaine est requise, le relecteur dispose de 48 heures. Si le
délai de réponse est strictement supérieur à 48 heures, la livraison est
reportée à la fenêtre suivante ; sinon elle est maintenue.

Borne : `delai_reponse_h > 48` · pas 1 · si vrai « reportée » · sinon « maintenue »

## R-05 — Décision nominative

Une décision de relecteur doit nommer la personne qui décide. Une décision
anonyme, ou dont le nom est vide, est refusée et redemandée ; elle n'est
jamais interprétée comme un accord.
