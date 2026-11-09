"""The test book as a CSV file for Xray's Test Case Importer (Jira). No Xray API is called.

Format from Xray's public documentation, "Importing Tests using Test Case
Importer" (XRAY_DOC_URL): one line per step, the lines of one test sharing the
same TCID; a column may be repeated to give a field several values (the
documentation's own example repeats Component). The columns are mapped to
Jira and Xray fields in the importer's wizard, so the names below are the
defaults the wizard proposes, not a fixed schema. Each case here is a manual
test with one step: Action, Data, Result.

Separator: semicolon, as in the documentation's example. Fields that contain
a semicolon or a quote are quoted by the csv module.
"""
import csv
import io

XRAY_DOC_URL = "https://docs.getxray.app/display/XRAYCLOUD/Importing+Tests+using+Test+Case+Importer"
XRAY_HEADER = ["TCID", "Summary", "Description", "Test Type", "Labels", "Labels", "Labels", "Action", "Data", "Result"]


def _data(inputs: dict) -> str:
    return ", ".join(f"{k}={'(absent)' if v is None else v}" for k, v in inputs.items())


def to_xray_csv(book: dict) -> str:
    spec = book["spec"]
    titles = {r["id"]: r["title"] for r in book["rules"]}
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(XRAY_HEADER)
    for case in book["cases"]:
        summary = f"{case['rule']} {titles.get(case['rule'], '')} — {case['kind']} — {_data(case['inputs'])}"[:255]
        origin = ("proposé par le modèle, vérifié par le code" if case["origin"] == "modèle"
                  else "écrit par le code depuis la borne de la règle")
        description = "\n".join([f"Spécification {spec['id']} version {spec['version'] or '?'}, règle {case['rule']}.",
                                 "Préconditions :", *[f"- {p}" for p in case["preconditions"]], f"Origine : {origin}."])
        writer.writerow([case["id"], summary, description, "Manual", spec["id"], case["rule"], case["kind"],
                         f"Appliquer la règle {case['rule']} aux données", _data(case["inputs"]), case["expected"]])
    return out.getvalue()
