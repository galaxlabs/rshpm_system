import frappe
from frappe import _

def _require_login():
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."), frappe.PermissionError)

def _require_system_user():
    # only system users
    user_type = frappe.db.get_value("User", frappe.session.user, "user_type")
    if user_type != "System User":
        frappe.throw(_("Not permitted."), frappe.PermissionError)

@frappe.whitelist()
def get_doctype_meta(doctype: str):
    """
    Safe DocType meta for staff portal (System Users only).
    Returns: fields + permissions.
    """
    _require_login()
    _require_system_user()

    if not doctype:
        frappe.throw(_("Missing doctype"))

    # Only allow your doctypes (prevent exposing everything)
    allowed = {
        "Client",
        "Property",
        "Inquiry",
        "Booking",
        "Payment",
        "Allotment",
        "Possession",
        "Property Ownership",
        "Ownership Transfer",
    }
    if doctype not in allowed:
        frappe.throw(_("DocType not allowed."), frappe.PermissionError)

    meta = frappe.get_meta(doctype)

    # return only safe parts needed by SPA
    fields = []
    for f in meta.fields:
        fields.append({
            "fieldname": f.fieldname,
            "fieldtype": f.fieldtype,
            "label": f.label,
            "options": f.options,
            "reqd": f.reqd,
            "read_only": f.read_only,
            "hidden": f.hidden,
        })

    return {
        "ok": True,
        "doctype": doctype,
        "fields": fields,
        "permissions": meta.permissions,  # role permission info
        "title_field": meta.title_field,
        "name_field": meta.name,
    }
