"""From a functional spec to a test book: the model proposes cases, the code checks them, computes the
bounds, fills the gaps and exports the book. No LLM runs here: a fake model answers."""
import csv
import io
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from fake_spec_model import FakeSpecModel, case
from regression_triage.spec_tests import __main__ as cli
from regression_triage.spec_tests.bounds import allowed_numbers, boundary_points, numbers_in
from regression_triage.spec_tests.check import build_book, check_case
from regression_triage.spec_tests.spec import Rounding, SpecError, Threshold, load_spec, parse_spec
from regression_triage.spec_tests.writer import OllamaCaseWriter, Proposal, prompt
from regression_triage.spec_tests.xray import XRAY_DOC_URL, XRAY_HEADER, to_xray_csv

SPECS = Path(__file__).resolve().parent.parent / "specs"
TRUTH = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (SPECS / "verite").glob("*.json")}
TOL, BLQ, ARR = load_spec(SPECS / "tolerance-ecarts-pv.md"), load_spec(SPECS / "blocage-livraison.md"), \
    load_spec(SPECS / "arrondi-montants.md")


def book_for(spec, cases=(), error=None):
    return build_book(spec, FakeSpecModel(cases, error).propose(spec))


def reasons(book):
    return [r["reason"] for r in book["rejected"]]


# --- Reading the spec ----------------------------------------------------------

@pytest.mark.parametrize("name", sorted(TRUTH))
def test_every_rule_of_every_spec_is_found_with_its_id(name):
    truth = TRUTH[name]
    spec = load_spec(SPECS / truth["file"])
    assert spec.id == truth["spec"]
    assert [r.id for r in spec.rules] == truth["rules"]
    assert all(r.title and r.text for r in spec.rules)


def test_a_bound_is_read_with_its_variable_operator_value_step_and_outcomes():
    bound = TOL.rule("R-01").bound
    assert isinstance(bound, Threshold)
    assert (bound.variable, bound.absolute, bound.op, bound.value, bound.step, bound.if_true, bound.if_false) == \
        ("ecart_pv", True, "<=", Decimal("50.00"), Decimal("0.01"), "accepté", "signalé")
    assert TOL.rule("R-05").bound is None              # prose only: no bound the code can compute
    rounding = ARR.rule("R-02").bound
    assert isinstance(rounding, Rounding) and (rounding.variable, rounding.decimals, rounding.mode) == \
        ("taux", 4, "demi-pair")


@pytest.mark.parametrize("line", ["Borne : `ecart_pv <== 50 EUR` · pas 0.01 · si vrai « a » · sinon « b »",
                                  "Borne : `ecart_pv <= 50 EUR` · si vrai « a » · sinon « b »",
                                  "Arrondi : `arrondi(montant, 2, au hasard)`"])
def test_a_bound_the_code_cannot_read_stops_the_run(line):
    with pytest.raises(SpecError):
        parse_spec(f"# SPEC-X — x\n\n## R-01 — Règle\n\nTexte.\n\n{line}\n")


def test_two_rules_with_the_same_id_stop_the_run():
    with pytest.raises(SpecError):
        parse_spec("# SPEC-X — x\n\n## R-01 — Une\n\nTexte.\n\n## R-01 — Deux\n\nTexte.\n")


# --- Bounds, computed by the code ----------------------------------------------

@pytest.mark.parametrize("name", sorted(TRUTH))
def test_the_bounds_computed_by_the_code_are_the_ones_written_by_hand(name):
    truth = TRUTH[name]
    spec = load_spec(SPECS / truth["file"])
    computed = {r.id: boundary_points(r) for r in spec.rules if r.bound}
    assert set(computed) == set(truth["bounds"])
    for rule_id, expected in truth["bounds"].items():
        at, beyond = computed[rule_id]
        assert (at.value, at.expected) == (Decimal(expected["limit"]), expected["limit_expected"])
        assert (beyond.value, beyond.expected) == (Decimal(expected["beyond"]), expected["beyond_expected"])


def test_a_strict_bound_is_tested_on_the_other_side():
    at, beyond = boundary_points(BLQ.rule("R-04"))    # delai_reponse_h > 48
    assert (at.value, at.expected, beyond.value, beyond.expected) == (48, "maintenue", 49, "reportée")


def test_numbers_are_read_whatever_their_writing_and_rule_ids_are_not_numbers():
    assert numbers_in("50,00 EUR selon R-01, puis 50.01 et 48h") == {Decimal("50"), Decimal("50.01"), Decimal("48")}


def test_the_allowed_numbers_are_the_spec_s_and_the_code_s():
    allowed = allowed_numbers(TOL)
    assert {Decimal("50"), Decimal("120"), Decimal("5")} <= allowed            # written in the spec
    assert {Decimal("50.01"), Decimal("4.99"), Decimal("120.01")} <= allowed   # computed by the code
    assert Decimal("42.37") not in allowed


# --- Checking each proposed case -------------------------------------------------

def test_a_case_that_cites_a_rule_the_spec_does_not_have_is_rejected_and_kept():
    book = book_for(TOL, [case("R-99", {"ecart_pv": "50.00"}, "accepté")])
    assert book["rejected"][0]["reason"] == "règle inconnue"
    assert "R-99" in book["rejected"][0]["why"]
    assert book["rejected"][0]["proposed"]["rule"] == "R-99"          # kept for the audit
    assert all(c["rule"] != "R-99" for c in book["cases"])


@pytest.mark.parametrize("bad", [
    case("R-01", {"ecart_pv": 42.37}, "accepté", kind="nominal"),                       # in the inputs
    case("R-01", {"ecart_pv": "50.00"}, "accepté, avec une marge de 3.5 EUR"),           # in the expected result
    case("R-01", {"ecart_pv": "50.00"}, "accepté", preconditions=["Portefeuille de 250 swaps"]),
])
def test_a_number_that_is_neither_in_the_spec_nor_computed_by_the_code_is_rejected(bad):
    book = book_for(TOL, [bad])
    assert reasons(book) == ["nombre inventé"]


def test_a_number_from_the_spec_or_a_bound_of_the_code_is_accepted():
    book = book_for(TOL, [case("R-01", {"ecart_pv": 50}, "L'écart est accepté", id="A"),
                          case("R-01", {"ecart_pv": "50,01"}, "L'écart est signalé", id="B")])
    assert book["rejected"] == []
    assert [c["origin"] for c in book["cases"] if c["rule"] == "R-01"] == ["modèle", "modèle"]


@pytest.mark.parametrize("value, expected", [("50.01", "L'écart est accepté"), ("50.00", "signalé"),
                                             ("-50.01", "accepté")])
def test_an_outcome_that_contradicts_the_bound_is_rejected(value, expected):
    book = book_for(TOL, [case("R-01", {"ecart_pv": value}, expected)])
    assert reasons(book) == ["issue contredite"]


def test_an_outcome_named_with_a_longer_label_is_not_confused_with_the_shorter_one():
    ok = book_for(BLQ, [case("R-01", {"score_risque": 9}, "La livraison n'est pas bloquée : non bloquée")])
    assert ok["rejected"] == []
    wrong = book_for(BLQ, [case("R-01", {"score_risque": 10}, "non bloquée")])
    assert reasons(wrong) == ["issue contredite"]


def test_a_bound_case_whose_outcome_names_neither_label_is_rejected():
    book = book_for(TOL, [case("R-01", {"ecart_pv": "50.00"}, "rien de particulier")])
    assert reasons(book) == ["issue absente"]


def test_a_wrong_rounding_is_rejected_and_a_right_one_is_kept():
    book = book_for(ARR, [case("R-01", {"montant": "0.005"}, "0.00", id="faux"),
                          case("R-02", {"taux": "0.03135"}, "Le taux publié est 0,0314", id="juste")])
    assert reasons(book) == ["arrondi faux"]
    assert book["rejected"][0]["proposed"]["id"] == "faux"
    assert any(c["proposed_id"] == "juste" and c["origin"] == "modèle" for c in book["cases"])


def test_a_malformed_case_is_rejected_alone():
    book = book_for(TOL, [{"rule": "R-01", "kind": "limite"},
                          case("R-01", {"ecart_pv": "50.00"}, "accepté", kind="au hasard"),
                          case("R-05", {"produit": "EQO"}, "signalé, tolérance absente", kind="erreur")])
    assert reasons(book) == ["schéma", "schéma"]
    assert any(c["rule"] == "R-05" and c["origin"] == "modèle" for c in book["cases"])


def test_a_duplicate_case_is_rejected():
    same = case("R-01", {"ecart_pv": "50.00"}, "accepté")
    book = book_for(TOL, [same, {**same, "id": "CT-02"}])
    assert reasons(book) == ["doublon"]


def test_check_case_says_why_and_never_raises():
    accepted, rejection = check_case("not a dict", TOL, allowed_numbers(TOL), set())
    assert accepted is None and rejection["reason"] == "schéma"


# --- Coverage: measured, then completed by the code ------------------------------

def test_without_any_case_the_code_writes_every_bound_and_signals_the_rest():
    book = book_for(TOL)
    by_rule = {}
    for c in book["cases"]:
        by_rule.setdefault(c["rule"], []).append(c)
    for rule_id in ("R-01", "R-02", "R-03", "R-04"):
        points = boundary_points(TOL.rule(rule_id))
        written = [(Decimal(str(c["inputs"][TOL.rule(rule_id).bound.variable])), c["expected"]) for c in by_rule[rule_id]]
        assert written == [(p.value, p.expected) for p in points]
        assert all(c["origin"] == "code" and c["kind"] == "limite" for c in by_rule[rule_id])
    assert [u["rule"] for u in book["coverage"]["uncovered"]] == ["R-05", "R-06"]
    assert book["coverage"]["rules_covered_by_model"] == 0 and book["coverage"]["rules_covered"] == 4


def test_the_code_only_adds_the_bound_the_model_missed():
    book = book_for(TOL, [case("R-04", {"rapport_tolerance": 5}, "significatif")])
    r04 = [c for c in book["cases"] if c["rule"] == "R-04"]
    assert [(c["origin"], str(c["inputs"]["rapport_tolerance"])) for c in r04] == [("modèle", "5"), ("code", "4.99")]
    assert book["coverage"]["bounds_by_model"] == 1
    assert book["coverage"]["bounds_added_by_code"] == book["coverage"]["bounds_required"] - 1


def test_a_rejected_case_does_not_count_for_coverage():
    book = book_for(TOL, [case("R-01", {"ecart_pv": "50.01"}, "accepté")])      # wrong outcome
    r01 = [c for c in book["cases"] if c["rule"] == "R-01"]
    assert [c["origin"] for c in r01] == ["code", "code"]
    assert book["coverage"]["rules_covered_by_model"] == 0


def test_a_rule_without_bound_is_covered_only_by_an_accepted_case():
    book = book_for(TOL, [case("R-06", {"valeur_candidate": None}, "signalé, valorisation en échec", kind="erreur")])
    assert [u["rule"] for u in book["coverage"]["uncovered"]] == ["R-05"]
    assert "humain" in book["coverage"]["uncovered"][0]["why"]


def test_a_model_that_fails_leaves_the_book_to_the_code_and_says_so():
    book = book_for(TOL, error="model call failed: ConnectionError")
    assert book["model_error"] == "model call failed: ConnectionError"
    assert all(c["origin"] == "code" for c in book["cases"]) and len(book["cases"]) == 8


def test_case_ids_are_unique_and_follow_the_spec():
    book = book_for(TOL, [case("R-01", {"ecart_pv": "50.00"}, "accepté", id="X"),
                          case("R-02", {"ecart_pv": "25.00"}, "accepté", id="X")])
    ids = [c["id"] for c in book["cases"]]
    assert len(ids) == len(set(ids)) and all(i.startswith("SPEC-TOL-") for i in ids)


# --- Xray export ----------------------------------------------------------------------

def test_the_xray_export_has_one_row_per_case_with_the_importer_columns():
    book = book_for(TOL, [case("R-05", {"produit": "EQO"}, "signalé ; tolérance absente", kind="erreur")])
    text = to_xray_csv(book)
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert rows[0] == XRAY_HEADER
    assert len(rows) == 1 + len(book["cases"])
    col = {name: rows[0].index(name) for name in ("TCID", "Summary", "Test Type", "Action", "Data", "Result")}
    assert [r[col["TCID"]] for r in rows[1:]] == [c["id"] for c in book["cases"]]
    assert {r[col["Test Type"]] for r in rows[1:]} == {"Manual"}
    r05 = next(r for r in rows[1:] if "R-05" in r[col["Summary"]])
    assert r05[col["Result"]] == "signalé ; tolérance absente"          # a separator inside a field is quoted
    assert r05[col["Data"]] == "produit=EQO"
    assert XRAY_DOC_URL.startswith("https://docs.getxray.app/")


# --- The model: called deterministically, given the spec, never the truth -------------------

class FakeOllama:
    def __init__(self, content):
        self.content, self.calls = content, []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(content=self.content), prompt_eval_count=12, eval_count=3)


def test_the_model_is_called_deterministically_without_tools():
    writer = OllamaCaseWriter()
    writer._client = FakeOllama(json.dumps({"cases": []}))
    assert writer.propose(TOL) == {"cases": []}
    call = writer._client.calls[0]
    assert call["options"]["temperature"] == 0 and call["options"]["seed"] == 42
    assert call["think"] is False and "tools" not in call
    assert call["format"] == Proposal.model_json_schema()


@pytest.mark.parametrize("content", ["not json", json.dumps({"cas": []}), json.dumps([1, 2])])
def test_an_unreadable_answer_is_an_error_never_a_book(content):
    writer = OllamaCaseWriter()
    writer._client = FakeOllama(content)
    assert set(writer.propose(TOL)) == {"error"}


def test_the_prompt_carries_the_spec_and_the_values_of_the_code_never_the_truth():
    with_values, without = prompt(TOL, give_values=True), prompt(TOL, give_values=False)
    assert "## R-04 — Bruit numérique" in with_values and "## R-04 — Bruit numérique" in without
    assert "50.01" in with_values and "50.01" not in without
    truth_words = [m["why"] for m in TRUTH["SPEC-TOL"]["must_cover"] if "variable" not in m]   # hand-written cases
    assert truth_words and not any(why in with_values for why in truth_words)


# --- The command line -------------------------------------------------------------------------

def test_the_command_line_writes_the_book_and_the_xray_file_and_says_a_human_is_needed(tmp_path, capsys):
    code = cli.main([str(SPECS / "tolerance-ecarts-pv.md"), "--judge", "fake", "--out", str(tmp_path)])
    assert code == 3                                                   # two rules left to a human
    book = json.loads((tmp_path / "SPEC-TOL.cahier.json").read_text(encoding="utf-8"))
    assert book["spec"]["id"] == "SPEC-TOL" and len(book["cases"]) == 8
    assert (tmp_path / "SPEC-TOL.xray.csv").read_text(encoding="utf-8").startswith("TCID;")
    assert "R-05" in capsys.readouterr().out


def test_the_command_line_refuses_an_unreadable_spec(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("# SPEC-X — x\n\n## R-01 — a\n\nTexte.\n\nBorne : `x <== 1` · pas 1 · si vrai « a » · sinon « b »\n",
                   encoding="utf-8")
    assert cli.main([str(bad), "--judge", "fake", "--out", str(tmp_path)]) == 1
