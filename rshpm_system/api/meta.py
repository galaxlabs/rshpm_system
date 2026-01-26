import frappe

ALLOWED_DOCTYPES = {
    "Property",
    "Client",
    "Booking",
    "Payment",
    "Block",
    "Housing Scheme",
}

@frappe.whitelist()
def get_doctype_meta(doctype: str):
    if not doctype or doctype not in ALLOWED_DOCTYPES:
        frappe.throw("Doctype not allowed")

    # Must be logged in
    if frappe.session.user == "Guest":
        frappe.throw("Login required")

    meta = frappe.get_meta(doctype)

    # Return only safe field info needed for UI
    fields = []
    for df in meta.fields:
        fields.append({
            "fieldname": df.fieldname,
            "label": df.label,
            "fieldtype": df.fieldtype,
            "options": df.options,
            "reqd": int(df.reqd or 0),
            "read_only": int(df.read_only or 0),
            "hidden": int(df.hidden or 0),
            "depends_on": df.depends_on,
            "mandatory_depends_on": df.mandatory_depends_on,
        })

    return {
        "doctype": doctype,
        "title_field": meta.title_field,
        "fields": fields,
    }
