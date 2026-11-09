"""python -m regression_triage.spec_tests <spec.md> [--judge fake|ollama] [--out DIR] [--no-values]

--judge ollama  the local model proposes the cases (default)
--judge fake    no model: the code writes what it can compute, and signals the rest
--no-values     do not give the model the test values computed by the code

Writes <out>/<SPEC-ID>.cahier.json and <out>/<SPEC-ID>.xray.csv.
Exit code: 0 every rule covered, 1 the spec cannot be read, 3 rules left for a human.
"""
import argparse
import json
import sys
from pathlib import Path

from ..judge import DEFAULT_MODEL
from .check import build_book
from .spec import SpecError, load_spec
from .writer import NoModel, OllamaCaseWriter
from .xray import XRAY_DOC_URL, to_xray_csv


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m regression_triage.spec_tests", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--judge", choices=["fake", "ollama"], default="ollama")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", type=Path, default=Path("runs") / "spec-tests")
    parser.add_argument("--no-values", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        spec = load_spec(args.spec)
    except (SpecError, OSError) as error:
        print(f"Spécification illisible : {error}")
        return 1
    writer = NoModel() if args.judge == "fake" else OllamaCaseWriter(model=args.model)
    proposal = writer.propose(spec, give_values=not args.no_values)
    model = None if args.judge == "fake" else {"name": args.model, "temperature": 0, "seed": writer.seed,
                                               "values_given": not args.no_values, "usage": writer.usage}
    book = build_book(spec, proposal, model)

    args.out.mkdir(parents=True, exist_ok=True)
    book_path, csv_path = args.out / f"{spec.id}.cahier.json", args.out / f"{spec.id}.xray.csv"
    book_path.write_text(json.dumps(book, indent=2, ensure_ascii=False), encoding="utf-8")
    csv_path.write_text(to_xray_csv(book), encoding="utf-8")

    cov = book["coverage"]
    print(f"{spec.id} — {spec.title} : {cov['rules_total']} règles")
    if book["model_error"]:
        print(f"  modèle : {book['model_error']}")
    print(f"  cas proposés {cov['proposed']}, gardés {cov['accepted']}, rejetés {cov['rejected']}"
          + (f" ({', '.join(f'{k} {v}' for k, v in cov['rejected_by_reason'].items())})" if cov["rejected"] else ""))
    for r in book["rejected"]:
        proposed = r["proposed"] if isinstance(r["proposed"], dict) else {}
        print(f"    rejeté {proposed.get('id', '?')} [{proposed.get('rule', '?')}] {r['reason']} : {r['why']}")
    print(f"  bornes : {cov['bounds_required']} points requis, {cov['bounds_by_model']} testés par le modèle, "
          f"{cov['bounds_added_by_code']} écrits par le code")
    print(f"  règles couvertes : {cov['rules_covered']}/{cov['rules_total']} "
          f"(par le modèle seul : {cov['rules_covered_by_model']})")
    for u in cov["uncovered"]:
        print(f"    À ÉCRIRE PAR UN HUMAIN {u['rule']} {u['title']} : {u['why']}")
    print(f"  cahier : {book_path}\n  Xray : {csv_path} (format : {XRAY_DOC_URL})")
    return 3 if cov["uncovered"] else 0


if __name__ == "__main__":
    sys.exit(main())
