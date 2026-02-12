import frappe

@frappe.whitelist()
def whoami():
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Not logged in", frappe.PermissionError)

    # Safe across versions
    full_name = frappe.db.get_value("User", user, "full_name") or user

    return {
        "user": user,
        "full_name": full_name,
        "roles": frappe.get_roles(user),
    }
