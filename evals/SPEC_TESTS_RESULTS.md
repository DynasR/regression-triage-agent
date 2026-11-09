# Spécification → cas de test : résultats mesurés

Évaluation du 10/11/2026, run `evals/runs/spec-tests-20261110-004638` (non
versionné, comme les autres runs). Code évalué : commit `57fb958`, arbre
propre. Modèle : `qwen3.5:9b` par Ollama 0.40.15 (client Python 0.6.3),
température 0, graine 42, `num_ctx` 8192, `num_predict` 4096, sans outil,
sur la même machine que les évaluations du tri (AMD RX 7800 XT).

4 spécifications × 2 modes × 3 répétitions = 24 appels au modèle, 502 s en
tout, 24 381 jetons en entrée et 35 301 en sortie, coût 0 €.

- **Mode « values »** (celui de la ligne de commande par défaut) : le modèle
  reçoit la spécification et les valeurs de test calculées par le code.
- **Mode « no-values »** : la spécification seule. Ce que le modèle trouve
  par lui-même.

Le juge est `specs/verite/<SPEC>.json`, écrit à la main, jamais lu par le
modèle ni par le paquet `spec_tests`.

Commande : `python evals/spec_tests_eval.py --reps 3`.

## Le tableau

Totaux sur les 3 répétitions (une spécification de 5 règles donne donc /15).

| Mode | Spec | Règles citées (modèle) | Règles gardées (modèle) | Règles couvertes (final) | Points de borne testés par le modèle | Points écrits par le code | Cas « incontournables » (modèle) | Cas « incontournables » (final) | Proposés | Rejetés | Mêmes cas | Même texte | s / appel |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| values | SPEC-TOL | 18/18 | 15/18 | 15/18 | 24/24 | 0 | 30/33 | 30/33 | 48 | 3 | oui | oui | 18.7 |
| values | SPEC-BLQ | 15/15 | 15/15 | 15/15 | 18/18 | 0 | 24/24 | 24/24 | 45 | 0 | oui | oui | 15.9 |
| values | SPEC-INC | 15/15 | 15/15 | 15/15 | 12/12 | 0 | 21/21 | 21/21 | 66 | 0 | oui | oui | 22.8 |
| values | SPEC-ARR | 6/12 | 6/12 | 6/12 | **0/12** | 12 | 6/24 | 18/24 | 36 | 21 | oui | oui | 17.0 |
| no-values | SPEC-TOL | 18/18 | 12/18 | 15/18 | 18/24 | 6 | 21/33 | 27/33 | 30 | 9 | oui | oui | 12.3 |
| no-values | SPEC-BLQ | 9/15 | 9/15 | 9/15 | 18/18 | 0 | 18/24 | 18/24 | 18 | 0 | oui | oui | 7.3 |
| no-values | SPEC-INC | **0/15** | 0/15 | 6/15 | 0/12 | 12 | 0/21 | 12/21 | 0 | 0 | oui | oui | 57.9 |
| no-values | SPEC-ARR | 12/12 | 12/12 | 12/12 | 0/12 | 12 | 12/24 | 24/24 | 36 | 12 | oui | oui | 15.6 |

En résumé :

| | values | no-values |
|---|---|---|
| Règles couvertes par le modèle seul (cas gardés) | 51/60 (85 %) | 33/60 (55 %) |
| Règles couvertes après le code | 51/60 | 42/60 |
| Points de borne testés par le modèle | 54/66 | 36/66 |
| Points de borne écrits par le code | 12 | 30 |
| Cas incontournables de la vérité, modèle seul | 81/102 (79 %) | 51/102 (50 %) |
| Cas incontournables de la vérité, après le code | 93/102 (91 %) | 81/102 (79 %) |
| Cas rejetés | 24 (18 nombre inventé, 6 doublon) | 21 (nombre inventé) |
| Réponses illisibles | 0 | 3 (SPEC-INC, les 3 fois) |
| Répétitions identiques (cas et texte brut) | 4/4 | 4/4 |

Lecture des bornes après le code : 66/66 dans les deux modes, **par
construction** (le gabarit écrit tout point manquant). Ce n'est pas un
mérite à mettre au crédit du système ; le chiffre qui compte est ce que le
modèle a fait seul, et ce que le code a dû écrire à sa place.

Extraction des règles et des bornes par le code : 0 écart avec la vérité
écrite à la main, sur les 4 spécifications (20 règles, 11 bornes).

## Ce qui a marché

- **Avec les valeurs du code, trois spécifications sur quatre sont
  couvertes par le modèle seul** : SPEC-BLQ et SPEC-INC à 100 % des cas
  incontournables, SPEC-TOL à 30/33. Le modèle teste aussi des choses que
  le code n'exige pas : le côté négatif d'une valeur absolue (-50.01), les
  cas d'erreur des règles en prose (priorité P0, P4, absente ; code produit
  inconnu ; décision anonyme).
- **Aucun cas gardé n'est faux**, d'après une relecture à la main de chaque
  cas gardé de chaque sortie distincte (les répétitions sont identiques :
  7 sorties distinctes, la huitième est illisible). Le contrôle de l'issue
  calculée n'a jamais eu à refuser un cas : le modèle n'a jamais écrit une
  issue fausse pour une valeur à la borne.
- **Déterminisme total** : pour chaque spécification et chaque mode, les
  trois répétitions donnent le même texte brut, octet pour octet.

## Les échecs, et leur cause

### 1. Le garde-fou de nombres a surtout refusé des cas justes

39 rejets « nombre inventé » au total. Relus un par un :

| Cas refusé | Mode | Fois | Le cas était-il juste ? |
|---|---|---|---|
| SPEC-TOL R-04 : `ecart_pv` 249.99 avec `rapport_tolerance` 4.99, sur une tolérance de 50 | no-values | 3 | **non** : 249.99 / 50 = 4.9998, pas 4.99. Un nombre que le modèle a calculé, et mal. |
| SPEC-TOL R-04 : `ecart_pv` 250.0 avec `rapport_tolerance` 5 | no-values | 3 | oui (5 × 50), mais le code ne peut pas savoir d'où vient 250 |
| SPEC-TOL R-05 : `ecart_pv` 0.0 pour un produit sans tolérance | les deux | 6 | oui : la spécification écrit « nul » en toutes lettres, pas 0 |
| SPEC-ARR R-01 : 2.3449 → 2.34, 2.3451 → 2.35, 2.355 → 2.36 | les deux | 12 | oui, arrondis justes |
| SPEC-ARR R-02 : 0.031249 → 0.0312, 0.031349 → 0.0313, 0.031251 → 0.0313, ±0.03145 → ±0.0314 | les deux | 15 | oui, arrondis justes, y compris le demi au pair |

**3 rejets sur 39 ont arrêté une vraie erreur ; 36 ont refusé un cas juste.**
C'est le prix du principe : le code refuse ce qu'il ne peut pas tracer,
même quand c'est juste. Le cahier n'y perd que là où le code ne rattrape
pas : la règle R-05 de SPEC-TOL n'est couverte par personne (signalée à un
humain, 6 fois), et les cas d'arrondi réalistes du modèle sont remplacés par
les points du code.

### 2. Les points d'arrondi du code sont artificiels

Le code place la borne d'un arrondi au premier mi-chemin à partir de zéro
(0.005 pour 2 décimales, 0.00005 pour 4). Le modèle, lui, est parti des
exemples de la spécification (2,345 ; 0,03125) et a construit autour d'eux
les bons voisins : juste en deçà, mi-chemin suivant. Ses arrondis étaient
justes ; ses nombres n'étaient pas dans la liste. Même avec les valeurs du
code sous les yeux, il ne les a pas utilisées (0/12 en mode values) : il a
repris les **noms** des points du code (« juste en deçà du mi-chemin »,
« mi-chemin suivant ») sur ses propres nombres. Ici, le testeur réaliste
était le modèle. Correction possible, non faite pour ne pas ajuster le code
sur ces quatre spécifications : ancrer les points d'arrondi sur le premier
exemple chiffré de la règle.

### 3. Donner les valeurs a fait oublier les règles en prose de SPEC-ARR

Sans les valeurs, le modèle couvre les 4 règles de SPEC-ARR (dont R-03, un
seul arrondi, et R-04, valeur absente). Avec les valeurs, il n'écrit plus que
des cas chiffrés et oublie R-03 et R-04, signalées à un humain. Sur SPEC-BLQ,
l'effet inverse : sans les valeurs, il oublie R-03 et R-05 (les deux règles
en prose), avec les valeurs, il les couvre.

### 4. Une boucle sur SPEC-INC sans les valeurs

Les trois fois, le modèle énumère des identifiants valides (`INC-000000`,
`INC-000001`… jusqu'à `INC-000051`) jusqu'à la limite de 4 096 jetons ; le
JSON est coupé, la réponse est illisible, 58 s perdues par appel. Le code
traite une réponse illisible comme aucune proposition : il écrit les 4 points
de borne et signale les 3 règles en prose. Même terminée, cette liste aurait
été refusée (des nombres inventés), et le cahier final de SPEC-INC sans
valeurs n'a que 12 cas incontournables sur 21.

### 5. Ce que le juge ne mesure pas

Pour une règle en prose, un cas « incontournable » est compté couvert dès
qu'un cas gardé cite la règle : le sens n'est pas jugé par code. La relecture
à la main dit qu'ils sont justes ; certains sont faibles (SPEC-TOL R-06 avec
des entrées vides, SPEC-ARR R-03 avec deux booléens).

## Ce que ces chiffres ne disent pas

- Quatre spécifications courtes, écrites par la même personne que la vérité
  et que le code. Pas de jeu tenu à l'écart.
- Le format des bornes (`Borne :`, `Arrondi :`) est fait pour ce code. Une
  vraie spécification en prose n'en a pas : le code n'y calculerait rien.
- L'export Xray suit la documentation publique du Test Case Importer ; il n'a
  pas été importé dans un vrai Jira.
