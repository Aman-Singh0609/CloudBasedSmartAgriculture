"""Derive per-crop temperature and pH limits from the FAO EcoCrop database.

    python scripts/build_ecocrop_limits.py path/to/EcoCrop_DB.csv

Writes ``app/data/ecocrop_limits.json`` (22 crops). The database is published
by FAO (https://gaez.fao.org/pages/ecocrop); a copy is redistributed by
OpenCLIM under the UK Open Government Licence v3.0. Only the absolute and
optimal temperature and pH limits are kept. Rainfall is deliberately left out:
EcoCrop gives annual totals, whereas the model's rainfall input is on a
different (shorter-period) scale, so the two cannot be compared.
"""
import csv
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "ecocrop_limits.json"
SPECIES = {
    "rice": "Oryza sativa", "maize": "Zea mays", "chickpea": "Cicer arietinum",
    "kidneybeans": "Phaseolus vulgaris", "pigeonpeas": "Cajanus cajan", "mothbeans": "Vigna aconitifolia",
    "mungbean": "Vigna radiata", "blackgram": "Vigna mungo", "lentil": "Lens culinaris",
    "pomegranate": "Punica granatum", "banana": "Musa acuminata", "mango": "Mangifera indica",
    "grapes": "Vitis vinifera", "watermelon": "Citrullus lanatus", "muskmelon": "Cucumis melo",
    "apple": "Malus domestica", "orange": "Citrus sinensis", "papaya": "Carica papaya",
    "coconut": "Cocos nucifera", "cotton": "Gossypium hirsutum", "jute": "Corchorus capsularis",
    "coffee": "Coffea arabica",
}
FIELDS = {"temperature_optimal": ("TOPMN", "TOPMX"), "temperature_absolute": ("TMIN", "TMAX"),
          "ph_optimal": ("PHOPMN", "PHOPMX"), "ph_absolute": ("PHMIN", "PHMAX")}


def main(path: str) -> None:
    rows = list(csv.DictReader(open(path, encoding="utf-8", errors="replace")))
    result = {}
    for crop, species in SPECIES.items():
        match = next(r for r in rows if r["ScientificName"].strip() == species)
        entry = {"species": species}
        for name, (lo, hi) in FIELDS.items():
            entry[name] = [float(match[lo]), float(match[hi])]
            assert entry[name][0] < entry[name][1], (crop, name)
        result[crop] = entry
    doc = {
        "source": "FAO EcoCrop database (https://gaez.fao.org/pages/ecocrop); copy under the UK Open Government Licence v3.0",
        "note": "Temperature in degrees C, soil pH. Absolute limits are where the crop can still survive or grow; optimal limits are where it does best.",
        "crops": result,
    }
    OUT.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(result)} crops)")


if __name__ == "__main__":
    main(sys.argv[1])
