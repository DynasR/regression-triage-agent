"""From a functional spec to a test book: the model proposes the cases, the code checks them.

    spec.md --(code)--> rules, ids, bounds --(model)--> proposed cases
            --(code)--> checked: rule exists, numbers traced, outcome computed at the bound
            --(code)--> coverage: the missing boundary points written from a template,
                        the rules nobody covered signalled to a human
            --> book.json + Xray CSV

spec.py    read the spec: rules, ids, bounds          (code)
bounds.py  boundary points and allowed numbers        (code)
writer.py  the model's proposal, settings, prompt     (model)
check.py   every check, the coverage, the template    (code)
xray.py    the CSV for Xray's Test Case Importer      (code)

Usage: python -m regression_triage.spec_tests specs/tolerance-ecarts-pv.md --judge ollama
"""
