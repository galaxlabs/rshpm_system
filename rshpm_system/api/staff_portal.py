import frappe
from frappe import _


# ---------- Role Menus (separate per role) ----------
MENU_BY_ROLE = {
    "Receptionist": [
        {"label": "Dashboard", "route": "/dashboard"},
        {"label": "Clients", "route": "/clients"},
        {"label": "Inquiries", "route": "/inquiries"},
        {"label": "Bookings", "route": "/bookings"},
        {"label": "Payments", "route": "/payments"},
        {"label": "Reports", "route": "/reports"},
    ],
    "Manager": [
        {"label": "Dashboard", "route": "/dashboard"},
        {"label": "Properties", "route": "/properties"},
        {"label": "Units", "route": "/units"},
        {"label": "Clients", "route": "/clients"},
        {"label": "Inquiries", "route": "/inquiries"},
        {"label": "Bookings", "route": "/bookings"},
        {"label": "Payments", "route": "/payments"},
        {"label": "Maintenance", "route": "/maintenance"},
        {"label": "Reports", "route": "/reports"},
        {"label": "Settings", "route": "/settings"},
    ],
}

# Keep this minimal for Phase 1; add more doctypes later (one-by-one)
PHASE1_DOCTYPES = ["Booking", "Payment", "Client", "Property", "Inquiry"]


# ---------- Guards ----------
def _require_login():
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."), frappe.PermissionError)


def _require_system_user():
    user_type = frappe.db.get_value("User", frappe.session.user, "user_type")
    if user_type != "System User":
        frappe.throw(_("Not permitted."), frappe.PermissionError)


# ---------- Helpers ----------
def _pick_active_role(roles: list[str]) -> str:
    # manual/fixed role rule (no switching)
    if "Manager" in roles:
        return "Manager"
    if "Receptionist" in roles:
        return "Receptionist"
    # fallback
    return roles[0] if roles else "Guest"


def _doctype_perms(doctype: str, user: str) -> dict:
    # Minimal flags needed for SPA buttons/forms
    return {
        "read": bool(frappe.has_permission(doctype, ptype="read", user=user)),
        "create": bool(frappe.has_permission(doctype, ptype="create", user=user)),
        "write": bool(frappe.has_permission(doctype, ptype="write", user=user)),
        "submit": bool(frappe.has_permission(doctype, ptype="submit", user=user)),
        "cancel": bool(frappe.has_permission(doctype, ptype="cancel", user=user)),
    }


def _internal_state_model(doctype: str) -> dict:
    """
    You are NOT using Frappe Workflow DocType.
    You run internal logic using:
      - docstatus
      - and (if exists) a Select field named 'status'
    We expose that model to React.
    """
    meta = frappe.get_meta(doctype)
    status_field = meta.get_field("status")

    if status_field and status_field.fieldtype == "Select":
        options = [
            o.strip()
            for o in (status_field.options or "").splitlines()
            if o.strip()
        ]
        return {
            "type": "status_field",
            "fieldname": "status",
            "options": options,
            "docstatus_supported": True,
        }

    return {
        "type": "docstatus_only",
        "docstatus_supported": True,
        "docstatus_meaning": {0: "Draft", 1: "Submitted", 2: "Cancelled"},
    }


# ---------- APIs ----------
@frappe.whitelist()
def boot():
    """
    Staff SPA boot endpoint.
    Returns role menu + doctype perms + internal state model.
    """
    _require_login()
    _require_system_user()

    user = frappe.session.user
    roles = frappe.get_roles(user) or []
    active_role = _pick_active_role(roles)

    return {
        "ok": True,
        "user": user,
        "roles": roles,
        "active_role": active_role,
        "menus": MENU_BY_ROLE.get(active_role, []),
        "doctypes": {
            dt: {
                "perms": _doctype_perms(dt, user),
                "state_model": _internal_state_model(dt),
            }
            for dt in PHASE1_DOCTYPES
        },
        "company_mode": "single",
    }


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
        "permissions": meta.permissions,
        "title_field": meta.title_field,
        "name_field": meta.name,
    }
@frappe.whitelist()
def get_allowed_actions(doctype: str, name: str):
    """
    Returns allowed actions for a specific document based on:
    - Frappe permissions (create/write/submit/cancel)
    - Docstatus
    - Your internal status field (if present)
    NOTE: This does NOT perform actions; only tells the SPA what to show.
    """
    _require_login()
    _require_system_user()

    if not doctype or not name:
        frappe.throw(_("Missing doctype or name"))

    # hard allowlist to avoid exposing everything
    allowed = {"Booking", "Payment", "Inquiry", "Client", "Property"}
    if doctype not in allowed:
        frappe.throw(_("DocType not allowed."), frappe.PermissionError)

    doc = frappe.get_doc(doctype, name)

    user = frappe.session.user
    perms = _doctype_perms(doctype, user)

    # Base rules from perms + docstatus
    # docstatus: 0 Draft, 1 Submitted, 2 Cancelled
    actions = {
        "can_read": perms["read"],
        "can_edit": perms["write"] and doc.docstatus == 0,
        "can_submit": perms["submit"] and doc.docstatus == 0,
        "can_cancel": perms["cancel"] and doc.docstatus == 1,
        "can_delete": perms["write"] and doc.docstatus == 0,
        "reasons": [],
    }

    # Internal status rules (only if Select field "status" exists)
    meta = frappe.get_meta(doctype)
    has_status = bool(meta.get_field("status"))
    if has_status:
        status = (getattr(doc, "status", None) or "").strip()

        # Minimal safe gating (NO guessing of your business logic beyond obvious)
        # If Cancelled/Completed/Transferred etc. -> block edits/submits by default.
        terminal_statuses = {"Cancelled", "Completed", "Transferred", "Closed", "Lost"}
        if status in terminal_statuses:
            actions["can_edit"] = False
            actions["can_submit"] = False
            actions["can_cancel"] = False
            actions["can_delete"] = False
            actions["reasons"].append(f"Status is terminal: {status}")

    return {
        "ok": True,
        "doctype": doctype,
        "name": name,
        "docstatus": doc.docstatus,
        "status": getattr(doc, "status", None) if has_status else None,
        "actions": actions,
    }
@frappe.whitelist()
def list_payments(
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    sort_by: str = "modified",
    sort_order: str = "desc",
    from_date: str | None = None,
    to_date: str | None = None,
    booking: str | None = None,
    client: str | None = None,
    docstatus: int | None = None,
):
    _require_login()
    _require_system_user()

    doctype = "Payment"
    user = frappe.session.user

    if not frappe.has_permission(doctype, ptype="read", user=user):
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    # sanitize paging
    try:
        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 200)
    except Exception:
        frappe.throw(_("Invalid paging"))

    # allowlist sort columns to prevent SQL injection
    allowed_sort = {"modified", "creation", "posting_date", "paid_amount", "name"}
    if sort_by not in allowed_sort:
        sort_by = "modified"
    sort_order = "asc" if (str(sort_order).lower() == "asc") else "desc"

    filters = {}

    # optional filters (only apply if provided)
    if booking:
        filters["booking"] = booking
    if client:
        filters["customer"] = client  # change if your fieldname differs
    if docstatus is not None and docstatus != "":
        filters["docstatus"] = int(docstatus)

    # date filters (adjust fieldname if your Payment uses different date field)
    # common patterns: posting_date / payment_date / date
    date_field = "posting_date"
    if from_date and to_date:
        filters[date_field] = ["between", [from_date, to_date]]
    elif from_date:
        filters[date_field] = [">=", from_date]
    elif to_date:
        filters[date_field] = ["<=", to_date]

    # search across name + booking + customer (fast + useful)
    or_filters = []
    if search:
        s = search.strip()
        if s:
            or_filters = [
                ["Payment", "name", "like", f"%{s}%"],
                ["Payment", "booking", "like", f"%{s}%"],
                ["Payment", "customer", "like", f"%{s}%"],  # change if needed
            ]

    fields = [
        "name",
        "posting_date",
        "customer",
        "booking",
        "paid_amount",
        "docstatus",
        "modified",
    ]

    # total count
    total = frappe.db.count(doctype, filters=filters, or_filters=or_filters)

    # rows
    start = (page - 1) * page_size
    rows = frappe.get_all(
        doctype,
        filters=filters,
        or_filters=or_filters,
        fields=fields,
        order_by=f"{sort_by} {sort_order}",
        limit_start=start,
        limit_page_length=page_size,
    )

    return {
        "ok": True,
        "page": page,
        "page_size": page_size,
        "total": total,
        "rows": rows,
    }
@frappe.whitelist()
def list_payments(
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    sort_by: str = "payment_date",
    sort_order: str = "desc",
    from_date: str | None = None,
    to_date: str | None = None,
    booking: str | None = None,
    customer: str | None = None,
    property: str | None = None,
    payment_mode: str | None = None,
    payment_type: str | None = None,
    docstatus: int | None = None,
):
    _require_login()
    _require_system_user()

    doctype = "Payment"
    user = frappe.session.user

    if not frappe.has_permission(doctype, ptype="read", user=user):
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    # sanitize paging
    try:
        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 200)
    except Exception:
        frappe.throw(_("Invalid paging"))

    # allowlist sort columns (prevents injection)
    allowed_sort = {
        "payment_date",
        "modified",
        "creation",
        "amount_paid",
        "name",
    }
    if sort_by not in allowed_sort:
        sort_by = "payment_date"
    sort_order = "asc" if str(sort_order).lower() == "asc" else "desc"

    filters = {}

    # exact filters (only if provided)
    if booking:
        filters["booking"] = booking
    if customer:
        filters["customer"] = customer
    if property:
        filters["property"] = property
    if payment_mode:
        filters["payment_mode"] = payment_mode
    if payment_type:
        filters["payment_type"] = payment_type
    if docstatus is not None and docstatus != "":
        filters["docstatus"] = int(docstatus)

    # date range on payment_date
    date_field = "payment_date"
    if from_date and to_date:
        filters[date_field] = ["between", [from_date, to_date]]
    elif from_date:
        filters[date_field] = [">=", from_date]
    elif to_date:
        filters[date_field] = ["<=", to_date]

    # search: name + receipt/invoice + booking + customer + property
    or_filters = []
    if search:
        s = search.strip()
        if s:
            or_filters = [
                ["Payment", "name", "like", f"%{s}%"],
                ["Payment", "receiptinvoice_number", "like", f"%{s}%"],
                ["Payment", "booking", "like", f"%{s}%"],
                ["Payment", "customer", "like", f"%{s}%"],
                ["Payment", "property", "like", f"%{s}%"],
            ]

    fields = [
        "name",
        "payment_date",
        "customer",
        "property",
        "booking",
        "amount_paid",
        "payment_mode",
        "payment_type",
        "received_by",
        "docstatus",
        "modified",
    ]

    start = (page - 1) * page_size

# rows (supports or_filters in get_list)
    rows = frappe.get_list(
        doctype,
        filters=filters,
        or_filters=or_filters,
        fields=fields,
        order_by=f"{sort_by} {sort_order}",
        limit_start=start,
        limit_page_length=page_size,
    )

    # total count (get_list with limit 0 returns only metadata in your version)
    total = frappe.db.count(doctype, filters=filters) if not or_filters else len(
        frappe.get_list(
            doctype,
            filters=filters,
            or_filters=or_filters,
            fields=["name"],
            limit_start=0,
            limit_page_length=0,   # get all names; ok for now
        )
    )


    return {
        "ok": True,
        "page": page,
        "page_size": page_size,
        "total": total,
        "rows": rows,
    }
