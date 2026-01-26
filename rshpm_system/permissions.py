import frappe

def _is_client_user(user: str) -> bool:
    if not user or user in ("Guest", "Administrator"):
        return False
    return "Client" in (frappe.get_roles(user) or [])

def _client_name_for_user(user: str):
    return frappe.db.get_value("Client", {"user": user}, "name")

def user_query(user):
    if not _is_client_user(user):
        return ""
    return f"`tabUser`.`name` = {frappe.db.escape(user)}"

def client_query(user):
    if not _is_client_user(user):
        return ""
    client = _client_name_for_user(user)
    if not client:
        return "1=0"
    return f"`tabClient`.`name` = {frappe.db.escape(client)}"

def booking_query(user):
    if not _is_client_user(user):
        return ""
    client = _client_name_for_user(user)
    if not client:
        return "1=0"
    return f"`tabBooking`.`customer` = {frappe.db.escape(client)}"

def payment_query(user):
    if not _is_client_user(user):
        return ""
    client = _client_name_for_user(user)
    if not client:
        return "1=0"
    return f"`tabPayment`.`customer` = {frappe.db.escape(client)}"

def allotment_query(user):
    if not _is_client_user(user):
        return ""
    client = _client_name_for_user(user)
    if not client:
        return "1=0"
    return f"`tabAllotment`.`client` = {frappe.db.escape(client)}"

def possession_query(user):
    if not _is_client_user(user):
        return ""
    client = _client_name_for_user(user)
    if not client:
        return "1=0"
    return f"`tabPossession`.`client` = {frappe.db.escape(client)}"

def ownership_query(user):
    if not _is_client_user(user):
        return ""
    client = _client_name_for_user(user)
    if not client:
        return "1=0"
    return f"`tabProperty Ownership`.`owner_client` = {frappe.db.escape(client)}"

def transfer_query(user):
    if not _is_client_user(user):
        return ""
    client = _client_name_for_user(user)
    if not client:
        return "1=0"
    return (
        f"(`tabOwnership Transfer`.`from_client` = {frappe.db.escape(client)} "
        f"OR `tabOwnership Transfer`.`to_client` = {frappe.db.escape(client)})"
    )
