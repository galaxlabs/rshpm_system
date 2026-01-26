import frappe

@frappe.whitelist()
def whoami():
    """
    Minimal "who am I" endpoint for SPA.
    Works for any logged-in user (System User).
    Returns basic identity info only.
    """
    if frappe.session.user == "Guest":
        frappe.throw("Not logged in", frappe.PermissionError)

    u = frappe.get_cached_doc("User", frappe.session.user)
    return {
        "user": u.name,
        "full_name": u.full_name,
        "user_type": u.user_type,
        "roles": frappe.get_roles(u.name),
    }