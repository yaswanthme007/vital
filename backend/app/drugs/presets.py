"""Common intraoperative anaesthesia agents with typical adult dosing, used
to pre-fill POST /api/sessions/{id}/drugs from a quick-select UI. Not tied to
any S0 Pydantic model — this is a new, additive GET /api/drugs/presets
endpoint, so plain camelCase dicts are returned directly."""

DRUG_PRESETS = [
    {"drugName": "Propofol", "dose": 150, "unit": "mg", "route": "IV", "rate": None, "rateUnit": None, "isReversal": False, "reversalOf": None},
    {"drugName": "Thiopental", "dose": 400, "unit": "mg", "route": "IV", "rate": None, "rateUnit": None, "isReversal": False, "reversalOf": None},
    {"drugName": "Ketamine", "dose": 100, "unit": "mg", "route": "IV", "rate": None, "rateUnit": None, "isReversal": False, "reversalOf": None},
    {"drugName": "Etomidate", "dose": 20, "unit": "mg", "route": "IV", "rate": None, "rateUnit": None, "isReversal": False, "reversalOf": None},
    {"drugName": "Fentanyl", "dose": 100, "unit": "mcg", "route": "IV", "rate": None, "rateUnit": None, "isReversal": False, "reversalOf": None},
    {"drugName": "Morphine", "dose": 10, "unit": "mg", "route": "IV", "rate": None, "rateUnit": None, "isReversal": False, "reversalOf": None},
    {"drugName": "Remifentanil", "dose": 50, "unit": "mcg", "route": "IV", "rate": 0.1, "rateUnit": "mcg/kg/min", "isReversal": False, "reversalOf": None},
    {"drugName": "Rocuronium", "dose": 50, "unit": "mg", "route": "IV", "rate": None, "rateUnit": None, "isReversal": False, "reversalOf": None},
    {"drugName": "Suxamethonium", "dose": 100, "unit": "mg", "route": "IV", "rate": None, "rateUnit": None, "isReversal": False, "reversalOf": None},
    {"drugName": "Sugammadex", "dose": 200, "unit": "mg", "route": "IV", "rate": None, "rateUnit": None, "isReversal": True, "reversalOf": "Rocuronium"},
    {"drugName": "Sevoflurane", "dose": 2, "unit": "%", "route": "Inhalational", "rate": None, "rateUnit": None, "isReversal": False, "reversalOf": None},
    {"drugName": "Isoflurane", "dose": 1.2, "unit": "%", "route": "Inhalational", "rate": None, "rateUnit": None, "isReversal": False, "reversalOf": None},
    {"drugName": "Ondansetron", "dose": 4, "unit": "mg", "route": "IV", "rate": None, "rateUnit": None, "isReversal": False, "reversalOf": None},
    {"drugName": "Dexamethasone", "dose": 8, "unit": "mg", "route": "IV", "rate": None, "rateUnit": None, "isReversal": False, "reversalOf": None},
    {"drugName": "Atropine", "dose": 0.6, "unit": "mg", "route": "IV", "rate": None, "rateUnit": None, "isReversal": False, "reversalOf": None},
    {"drugName": "Ephedrine", "dose": 6, "unit": "mg", "route": "IV", "rate": None, "rateUnit": None, "isReversal": False, "reversalOf": None},
    {"drugName": "Neostigmine", "dose": 2.5, "unit": "mg", "route": "IV", "rate": None, "rateUnit": None, "isReversal": True, "reversalOf": "Rocuronium"},
]
