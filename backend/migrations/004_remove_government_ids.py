"""004 - Remove government ID numbers and phone numbers from user accounts.

Decision record
---------------
The original signup form collected a government ID type and number (Aadhaar,
PAN or driving licence) plus a phone number, and stored them in plain text.
Nothing in the product ever used them: there is no verification step, no
feature keyed on identity, and no legal requirement for an advisory tool.
Holding national identifiers without a purpose is pure liability, so the
fields were removed from signup and this migration deletes any stored values.

This is IRREVERSIBLE by design: there is no down(). The runner will not apply
it to a database that actually contains such values unless the operator
passes ``--allow-irreversible``. Take a backup first if the organisation has
any reason to retain the data (and store that backup accordingly).
"""

VERSION = 4
DESCRIPTION = "Delete government ID and phone number fields from user accounts (irreversible)"
REVERSIBLE = False

_FIELDS = ("govt_id", "govt_id_type", "phone")
_QUERY = {"$or": [{field: {"$exists": True}} for field in _FIELDS]}


def plan(db) -> dict:
    return {
        "affected_documents": db["users"].count_documents(_QUERY),
        "fields_removed": list(_FIELDS),
    }


def up(db) -> dict:
    result = db["users"].update_many(_QUERY, {"$unset": {field: "" for field in _FIELDS}})
    return {"users_updated": result.modified_count, "fields_removed": list(_FIELDS)}
