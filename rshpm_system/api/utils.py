import frappe
from frappe import _
from frappe.model.workflow import get_workflow_name

# ---- Role menus (separate per role) ----
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

PHASE1_DOCTYPES = [
    "Client",
    "Inquiry",
    "Booking",
    "Payment",
    "Property",
]

def _pick_active_role(roles: list[str]) -> str:
    # manual/fixed rule: if user has both, prefer Manager
    if "Manager" in roles:
        return "Manager"
    if "Receptionist" in roles:
        return "Receptionist"
    # fallback: first non-standard role if possible
    for r in roles:
        if r not in ("All", "Guest"):
            return r
    return roles[0] if roles else "Guest"

def _doctype_perms(doctype: str, user: str) -> dict:
    # Keep minimal flags for UI
    return {
        "read": bool(frappe.has_permission(doctype, ptype="read", user=user)),
        "create": bool(frappe.has_permission(doctype, ptype="create", user=user)),
        "write": bool(frappe.has_permission(doctype, ptype="write", user=user)),
        "submit": bool(frappe.has_permission(doctype, ptype="submit", user=user)),
        "cancel": bool(frappe.has_permission(doctype, ptype="cancel", user=user)),
    }

def _workflow_enabled(doctype: str) -> bool:
    # True if an active workflow exists for this DocType
    return bool(get_workflow_name(doctype))

@frappe.whitelist()
def boot():
    """
    Staff SPA boot endpoint:
    - Requires logged-in System User
    - Returns role, menus, doctype perms, workflow availability
    """
    _require_login()
    _require_system_user()

    user = frappe.session.user
    roles = frappe.get_roles(user) or []
    active_role = _pick_active_role(roles)

    return {
        "user": user,
        "roles": roles,
        "active_role": active_role,
        "menus": MENU_BY_ROLE.get(active_role, []),
        "doctypes": {
            dt: {
                "perms": _doctype_perms(dt, user),
                "workflow_enabled": _workflow_enabled(dt),
            }
            for dt in PHASE1_DOCTYPES
        },
        "company_mode": "single",
    }

# # rshpm_system/api/utils.py

# import re
# import frappe
# from frappe import _

# PHONE_RE = re.compile(r"\D+")

# def normalize_phone(phone: str) -> str:
# 	"""
# 	Normalize phone into digits-only.
# 	Example: +92 300-1234567 -> 923001234567
# 	"""
# 	p = PHONE_RE.sub("", (phone or "").strip())
# 	if p.startswith("0"):
# 		# Pakistan common: 03xx... -> 923xx...
# 		p = "92" + p[1:]
# 	return p

# def normalize_cnic(cnic: str) -> str:
# 	"""
# 	Normalize CNIC into digits-only: 35202-1234567-1 -> 3520212345671
# 	"""
# 	return PHONE_RE.sub("", (cnic or "").strip())

# def mask_phone(phone: str) -> str:
# 	p = normalize_phone(phone)
# 	if len(p) < 6:
# 		return "***"
# 	return p[:3] + "*****" + p[-3:]

# def get_client_for_user(user: str):
# 	"""
# 	Returns Client name mapped to this user, else None.
# 	Requires Client.user field (Link to User).
# 	"""
# 	return frappe.db.get_value("Client", {"user": user}, "name")

# def require_portal_user():
# 	if frappe.session.user == "Guest":
# 		frappe.throw(_("Login required"), frappe.PermissionError)

# def ensure_client_role(user: str):
# 	"""
# 	Make sure user has Client role.
# 	"""
# 	if not frappe.db.exists("Has Role", {"parent": user, "role": "Client"}):
# 		u = frappe.get_doc("User", user)
# 		u.add_roles("Client")
# 		u.save(ignore_permissions=True)

# def cache() -> "frappe.utils.redis_wrapper.RedisWrapper":
# 	return frappe.cache()

# def cache_key(*parts) -> str:
# 	return "rshpm:" + ":".join([str(p) for p in parts if p is not None])

# def rate_limit_or_throw(key: str, limit: int, window_seconds: int, message: str):
# 	"""
# 	Simple fixed-window rate limit:
# 	- increments a counter in redis for key
# 	- expires after window_seconds
# 	"""
# 	c = cache()
# 	val = c.get_value(key)
# 	try:
# 		val = int(val) if val is not None else 0
# 	except Exception:
# 		val = 0

# 	val += 1
# 	if val == 1:
# 		c.set_value(key, val, expires_in_sec=window_seconds)
# 	else:
# 		# keep existing expiry
# 		c.set_value(key, val)

# 	if val > limit:
# 		frappe.throw(message)

# def log_event(title: str, data: dict):
# 	"""
# 	Lightweight logging into Error Log (works without creating DocTypes).
# 	"""
# 	try:
# 		frappe.log_error(message=frappe.as_json(data), title=title)
# 	except Exception:
# 		pass
