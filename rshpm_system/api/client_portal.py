# rshpm_system/api/client_portal.py

import random
import frappe
from frappe import _
from frappe.utils import now_datetime, add_to_date, get_datetime, flt

from rshpm_system.api.utils import (
	normalize_phone, normalize_cnic, mask_phone,
	get_client_for_user, require_portal_user, ensure_client_role,
	cache_key, cache, rate_limit_or_throw, log_event
)

# -------------------------
# OTP settings
# -------------------------
OTP_TTL_SECONDS = 5 * 60            # 5 minutes
OTP_RESEND_LIMIT = 5                # per 30 minutes
OTP_RESEND_WINDOW = 30 * 60
OTP_VERIFY_LIMIT = 10               # per 30 minutes
OTP_VERIFY_WINDOW = 30 * 60


def _otp_generate() -> str:
	return f"{random.randint(100000, 999999)}"

def _otp_store(phone: str, otp: str):
	c = cache()
	c.set_value(cache_key("otp", phone), otp, expires_in_sec=OTP_TTL_SECONDS)
	c.set_value(cache_key("otp_ts", phone), str(now_datetime()), expires_in_sec=OTP_TTL_SECONDS)

def _otp_get(phone: str):
	c = cache()
	return c.get_value(cache_key("otp", phone))

def _otp_clear(phone: str):
	c = cache()
	c.delete_value(cache_key("otp", phone))
	c.delete_value(cache_key("otp_ts", phone))


def _ip():
	# works behind reverse proxy if headers set; otherwise fallback
	try:
		return frappe.local.request_ip or "unknown"
	except Exception:
		return "unknown"


def _send_otp_stub(phone: str, otp: str):
	"""
	REPLACE THIS with your real SMS gateway later.
	For now, we log OTP into Error Log (dev mode).
	"""
	log_event("PORTAL_OTP", {"phone": phone, "otp": otp})


def _ensure_client_user_field_exists():
	# safety: if field not created yet, fail clearly
	if not frappe.db.has_column("tabClient", "user"):
		frappe.throw("Client DocType must have field `user` (Link to User, unique).")


# ==========================================================
#  A) Signup + OTP (Guest endpoints)
# ==========================================================

@frappe.whitelist(allow_guest=True)
def signup_request(phone: str):
	"""
	Guest endpoint
	Request OTP for signup/login.
	Rate-limited by phone + IP.
	"""
	phone_n = normalize_phone(phone)
	if not phone_n or len(phone_n) < 10:
		frappe.throw("Invalid phone number.")

	ip = _ip()

	# rate limit
	rate_limit_or_throw(
		cache_key("otp_send_phone", phone_n),
		OTP_RESEND_LIMIT,
		OTP_RESEND_WINDOW,
		"Too many OTP requests. Please try later."
	)
	rate_limit_or_throw(
		cache_key("otp_send_ip", ip),
		OTP_RESEND_LIMIT * 2,
		OTP_RESEND_WINDOW,
		"Too many OTP requests from your network. Please try later."
	)

	otp = _otp_generate()
	_otp_store(phone_n, otp)
	_send_otp_stub(phone_n, otp)

	return {
		"ok": True,
		"message": f"OTP sent to {mask_phone(phone_n)}",
		"ttl_seconds": OTP_TTL_SECONDS
	}


@frappe.whitelist(allow_guest=True)
def signup_verify(phone: str, otp: str, full_name: str = None, cnic: str = None, email: str = None):
	"""
	Guest endpoint
	Verify OTP and create (or link) portal user + client.
	- If Client exists by phone/cnic, it links it.
	- Else creates new Client.
	- Creates User as Website User and assigns role "Client".
	Returns: {ok, user, client}
	"""
	_ensure_client_user_field_exists()

	phone_n = normalize_phone(phone)
	if not phone_n:
		frappe.throw("Invalid phone number.")

	ip = _ip()
	rate_limit_or_throw(
		cache_key("otp_verify_phone", phone_n),
		OTP_VERIFY_LIMIT,
		OTP_VERIFY_WINDOW,
		"Too many OTP attempts. Please try later."
	)
	rate_limit_or_throw(
		cache_key("otp_verify_ip", ip),
		OTP_VERIFY_LIMIT * 2,
		OTP_VERIFY_WINDOW,
		"Too many OTP attempts from your network. Please try later."
	)

	stored = _otp_get(phone_n)
	if not stored:
		frappe.throw("OTP expired. Please request again.")
	if (otp or "").strip() != str(stored).strip():
		frappe.throw("Invalid OTP.")

	# OTP good: clear
	_otp_clear(phone_n)

	cnic_n = normalize_cnic(cnic) if cnic else ""
	email = (email or "").strip().lower() or None

	# choose user id (email if provided; else phone-based)
	# Use deterministic user id for phone signups:
	user_id = email or f"{phone_n}@client.local"

	# Find existing client: by phone/cnic
	client_name = None
	if cnic_n:
		client_name = frappe.db.get_value("Client", {"cnic_normalized": cnic_n}, "name")
	if not client_name:
		client_name = frappe.db.get_value("Client", {"phone_normalized": phone_n}, "name")

	# Create or update User
	if not frappe.db.exists("User", user_id):
		u = frappe.get_doc({
			"doctype": "User",
			"email": user_id,
			"first_name": (full_name or "Client").strip()[:140],
			"enabled": 1,
			"send_welcome_email": 0,
			"user_type": "Website User",
		})
		u.insert(ignore_permissions=True)
	else:
		u = frappe.get_doc("User", user_id)
		if u.user_type != "Website User":
			# prevent linking staff account
			frappe.throw("This account is not allowed for client portal.", title="Not Allowed")

	ensure_client_role(user_id)

	# Create or update Client record
	if client_name:
		client = frappe.get_doc("Client", client_name)
	else:
		client = frappe.new_doc("Client")
		client.full_name = (full_name or "Client").strip()
		client.mobile_number = phone_n
		client.phone_normalized = phone_n
		if cnic_n:
			client.cnic_number = cnic_n
			client.cnic_normalized = cnic_n

	# Link user to client (single source of truth)
	client.user = user_id

	# Keep normalized fields in sync (even for existing client)
	if hasattr(client, "phone_normalized"):
		client.phone_normalized = phone_n
	if cnic_n and hasattr(client, "cnic_normalized"):
		client.cnic_normalized = cnic_n

	client.save(ignore_permissions=True)

	log_event("PORTAL_SIGNUP_VERIFY", {
		"user": user_id,
		"client": client.name,
		"phone": phone_n,
		"cnic": cnic_n[:6] + "..." if cnic_n else None,
		"ip": ip
	})

	return {
		"ok": True,
		"user": user_id,
		"client": client.name
	}


# ==========================================================
#  B) “My Data” APIs (Authenticated)
# ==========================================================

def _current_client_or_throw():
	require_portal_user()
	client = get_client_for_user(frappe.session.user)
	if not client:
		frappe.throw("No Client is linked to this user. Contact support.", title="Client Not Linked")
	return client

def _ensure_portal_user_for_client(client_name: str, fallback_phone: str = None) -> str:
	"""
	If Client exists but client.user is missing, create/link a Website User safely.
	Returns user_id.
	"""
	client = frappe.get_doc("Client", client_name)

	user_id = (getattr(client, "user", None) or "").strip()
	if user_id and frappe.db.exists("User", user_id):
		u = frappe.get_doc("User", user_id)
		if u.user_type != "Website User":
			frappe.throw("Linked user is not a portal user. Contact support.", title="Not Allowed")
		ensure_client_role(user_id)
		return user_id

	# Create deterministic portal user id
	phone_n = normalize_phone(getattr(client, "phone_normalized", None) or getattr(client, "mobile_number", None) or fallback_phone)
	if not phone_n:
		frappe.throw("Client phone is missing. Contact support.", title="Missing Phone")

	new_user_id = f"{phone_n}@client.local"

	if not frappe.db.exists("User", new_user_id):
		full_name = (getattr(client, "full_name", None) or "Client").strip()[:140]
		u = frappe.get_doc({
			"doctype": "User",
			"email": new_user_id,
			"first_name": full_name,
			"enabled": 1,
			"send_welcome_email": 0,
			"user_type": "Website User",
		})
		u.insert(ignore_permissions=True)

	ensure_client_role(new_user_id)

	# Link client.user
	client.user = new_user_id
	# Keep normalized phone
	if hasattr(client, "phone_normalized") and phone_n:
		client.phone_normalized = phone_n
	client.save(ignore_permissions=True)

	log_event("PORTAL_AUTO_LINK_USER", {
		"client": client.name,
		"user": new_user_id,
		"phone": phone_n
	})

	return new_user_id


@frappe.whitelist()
def my_profile():
	client = _current_client_or_throw()
	doc = frappe.get_doc("Client", client)

	# return only safe fields
	return {
		"ok": True,
		"client": {
			"name": doc.name,
			"full_name": getattr(doc, "full_name", None),
			"father_name": getattr(doc, "father_name", None),
			"mobile_number": getattr(doc, "mobile_number", None),
			"email_address": getattr(doc, "email_address", None),
			"address": getattr(doc, "address", None),
			"kyc_status": getattr(doc, "kyc_status", None),
			"risk_flags": getattr(doc, "risk_flags", None),
		}
	}


@frappe.whitelist()
def my_bookings():
	client = _current_client_or_throw()

	rows = frappe.get_all(
		"Booking",
		filters={"customer": client, "docstatus": ["<", 2]},
		fields=[
			"name", "booking_date", "property", "company",
			"total_cost", "discount", "net_total",
			"total_paid", "remaining_balance", "next_due_date", "overdue_amount",
			"status"
		],
		order_by="modified desc",
	)

	return {"ok": True, "bookings": rows}


@frappe.whitelist()
def my_booking_detail(booking: str):
	client = _current_client_or_throw()

	b = frappe.get_doc("Booking", booking)
	if b.customer != client:
		frappe.throw("Not permitted.", frappe.PermissionError)

	# schedule rows (safe)
	schedule = []
	for r in (b.installment_schedule or []):
		schedule.append({
			"installment_name": r.installment_name,
			"due_date": r.due_date,
			"due_amount": r.due_amount,
			"paid_amount": r.paid_amount,
			"status": r.status,
		})

	return {
		"ok": True,
		"booking": {
			"name": b.name,
			"booking_date": b.booking_date,
			"company": b.company,
			"property": b.property,
			"status": b.status,
			"total_cost": b.total_cost,
			"discount": b.discount,
			"net_total": b.net_total,
			"down_payment_amount": b.down_payment_amount,
			"down_payment_paid": b.down_payment_paid,
			"installments_paid_total": b.installments_paid_total,
			"total_paid": b.total_paid,
			"remaining_balance": b.remaining_balance,
			"next_due_date": b.next_due_date,
			"overdue_amount": b.overdue_amount,
			"advance_amount": b.advance_amount,
			"payment_plan_type": b.payment_plan_type,
			"installment_interval": b.installment_interval,
			"total_installments": b.total_installments,
			"grace_days": b.grace_days,
			"penalty_rule": b.penalty_rule,
		},
		"installment_schedule": schedule
	}


@frappe.whitelist()
def my_payments(booking: str = None):
	client = _current_client_or_throw()

	filters = {"customer": client, "docstatus": ["<", 2]}
	if booking:
		filters["booking"] = booking

	rows = frappe.get_all(
		"Payment",
		filters=filters,
		fields=[
			"name", "payment_date", "booking", "amount_paid",
			"payment_mode", "payment_type", "receiptinvoice_number",
			"reference_no", "unallocated_amount", "is_advance"
		],
		order_by="payment_date desc, modified desc"
	)

	return {"ok": True, "payments": rows}


@frappe.whitelist()
def my_payment_detail(payment: str):
	client = _current_client_or_throw()
	p = frappe.get_doc("Payment", payment)

	if p.customer != client:
		frappe.throw("Not permitted.", frappe.PermissionError)

	alloc = []
	for a in (p.payment_allocation or []):
		alloc.append({
			"installment_name": a.installment_name,
			"due_date": a.due_date,
			"allocated_amount": a.allocated_amount,
			"allocated_date": a.allocated_date,
			"note": getattr(a, "note", None),
			"attachments_typ": getattr(a, "attachments_typ", None),
			"attach_image": getattr(a, "attach_image", None),
			"attach_file": getattr(a, "attach_file", None),
		})

	return {
		"ok": True,
		"payment": {
			"name": p.name,
			"payment_date": p.payment_date,
			"booking": p.booking,
			"amount_paid": p.amount_paid,
			"payment_mode": p.payment_mode,
			"payment_type": p.payment_type,
			"receiptinvoice_number": p.receiptinvoice_number,
			"reference_no": p.reference_no,
			"is_advance": p.is_advance,
			"unallocated_amount": p.unallocated_amount,
		},
		"allocation": alloc
	}


@frappe.whitelist()
def my_allotments():
	client = _current_client_or_throw()
	rows = frappe.get_all(
		"Allotment",
		filters={"client": client, "docstatus": ["<", 2]},
		fields=["name", "allotment_date", "booking", "property", "company", "total_price"],
		order_by="modified desc"
	)
	return {"ok": True, "allotments": rows}


@frappe.whitelist()
def my_possessions():
	client = _current_client_or_throw()
	rows = frappe.get_all(
		"Possession",
		filters={"client": client, "docstatus": ["<", 2]},
		fields=["name", "possession_date", "booking", "property", "company"],
		order_by="modified desc"
	)
	return {"ok": True, "possessions": rows}


@frappe.whitelist()
def my_transfers():
	client = _current_client_or_throw()
	rows = frappe.get_all(
		"Ownership Transfer",
		filters=[
			["Ownership Transfer", "docstatus", "<", 2],
			["Ownership Transfer", "from_client", "=", client],
		],
		fields=["name", "property", "from_client", "to_client", "transfer_date", "transfer_fee", "company"],
		order_by="modified desc"
	)

	rows2 = frappe.get_all(
		"Ownership Transfer",
		filters=[
			["Ownership Transfer", "docstatus", "<", 2],
			["Ownership Transfer", "to_client", "=", client],
		],
		fields=["name", "property", "from_client", "to_client", "transfer_date", "transfer_fee", "company"],
		order_by="modified desc"
	)

	# merge unique
	seen = set()
	out = []
	for r in (rows + rows2):
		if r.name in seen:
			continue
		seen.add(r.name)
		out.append(r)

	return {"ok": True, "transfers": out}


@frappe.whitelist()
def my_properties():
	"""
	Return properties owned by client (based on Property Ownership active/closed).
	"""
	client = _current_client_or_throw()

	ownerships = frappe.get_all(
		"Property Ownership",
		filters={"owner_client": client},
		fields=["name", "property", "ownership_status", "ownership_start_date", "ownership_end_date"],
		order_by="ownership_start_date desc"
	)

	# optional: fetch property meta
	props = []
	prop_names = [o.property for o in ownerships if o.property]
	if prop_names:
		props = frappe.get_all(
			"Property",
			filters={"name": ["in", prop_names]},
			fields=["name", "plot_number", "block", "housing_scheme", "status", "unit_type", "area_value", "area_unit", "total_cost", "unique_id"],
		)

	prop_map = {p.name: p for p in props}
	out = []
	for o in ownerships:
		p = prop_map.get(o.property)
		out.append({
			"ownership": o,
			"property": p
		})

	return {"ok": True, "items": out}

@frappe.whitelist(allow_guest=True)
def otp_login_request(phone: str, cnic: str = None):
	"""
	Guest endpoint
	Request OTP for login (existing clients only).
	Extra security: CNIC match required if client has CNIC on record.
	Auto-fix: if client exists but user missing, create/link portal user.
	"""
	phone_n = normalize_phone(phone)
	if not phone_n or len(phone_n) < 10:
		frappe.throw("Invalid phone number.")

	ip = _ip()

	# Rate limit
	rate_limit_or_throw(
		cache_key("otp_login_send_phone", phone_n),
		OTP_RESEND_LIMIT,
		OTP_RESEND_WINDOW,
		"Too many OTP requests. Please try later."
	)
	rate_limit_or_throw(
		cache_key("otp_login_send_ip", ip),
		OTP_RESEND_LIMIT * 2,
		OTP_RESEND_WINDOW,
		"Too many OTP requests from your network. Please try later."
	)

	_ensure_client_user_field_exists()

	client_name = frappe.db.get_value("Client", {"phone_normalized": phone_n}, "name")
	if not client_name:
		frappe.throw("No client found for this phone. Please sign up first.", title="Not Registered")

	client = frappe.get_doc("Client", client_name)

	# CNIC check (if CNIC exists in record, user must provide matching CNIC)
	stored_cnic = normalize_cnic(getattr(client, "cnic_normalized", None) or getattr(client, "cnic_number", None) or "")
	input_cnic = normalize_cnic(cnic or "")

	if stored_cnic:
		if not input_cnic:
			frappe.throw("CNIC is required for login.", title="CNIC Required")
		if input_cnic != stored_cnic:
			frappe.throw("CNIC does not match our record.", title="CNIC Mismatch")

	# Ensure/auto-link portal user
	user_id = _ensure_portal_user_for_client(client.name, fallback_phone=phone_n)

	u = frappe.get_doc("User", user_id)
	if u.user_type != "Website User":
		frappe.throw("This account is not allowed for client portal.", title="Not Allowed")

	otp = _otp_generate()
	_otp_store(phone_n, otp)
	_send_otp_stub(phone_n, otp)

	log_event("PORTAL_OTP_LOGIN_REQUEST", {
		"user": user_id,
		"client": client.name,
		"phone": phone_n,
		"ip": ip
	})

	return {
		"ok": True,
		"message": f"OTP sent to {mask_phone(phone_n)}",
		"ttl_seconds": OTP_TTL_SECONDS
	}


@frappe.whitelist(allow_guest=True)
def otp_login_verify(phone: str, otp: str, cnic: str = None):
	"""
	Guest endpoint
	Verify OTP and create a logged-in session (passwordless).
	Extra security: CNIC match required if client has CNIC on record.
	Auto-fix: if client exists but user missing, create/link portal user.
	"""
	phone_n = normalize_phone(phone)
	if not phone_n:
		frappe.throw("Invalid phone number.")

	ip = _ip()

	# Rate limit verify attempts
	rate_limit_or_throw(
		cache_key("otp_login_verify_phone", phone_n),
		OTP_VERIFY_LIMIT,
		OTP_VERIFY_WINDOW,
		"Too many OTP attempts. Please try later."
	)
	rate_limit_or_throw(
		cache_key("otp_login_verify_ip", ip),
		OTP_VERIFY_LIMIT * 2,
		OTP_VERIFY_WINDOW,
		"Too many OTP attempts from your network. Please try later."
	)

	stored = _otp_get(phone_n)
	if not stored:
		frappe.throw("OTP expired. Please request again.")
	if (otp or "").strip() != str(stored).strip():
		frappe.throw("Invalid OTP.")

	# OTP good: clear
	_otp_clear(phone_n)

	_ensure_client_user_field_exists()

	client_name = frappe.db.get_value("Client", {"phone_normalized": phone_n}, "name")
	if not client_name:
		frappe.throw("No client found for this phone. Please sign up first.", title="Not Registered")

	client = frappe.get_doc("Client", client_name)

	# CNIC check (if CNIC exists in record, user must provide matching CNIC)
	stored_cnic = normalize_cnic(getattr(client, "cnic_normalized", None) or getattr(client, "cnic_number", None) or "")
	input_cnic = normalize_cnic(cnic or "")

	if stored_cnic:
		if not input_cnic:
			frappe.throw("CNIC is required for login.", title="CNIC Required")
		if input_cnic != stored_cnic:
			frappe.throw("CNIC does not match our record.", title="CNIC Mismatch")

	# Ensure/auto-link portal user
	user_id = _ensure_portal_user_for_client(client.name, fallback_phone=phone_n)

	u = frappe.get_doc("User", user_id)
	if u.user_type != "Website User":
		frappe.throw("This account is not allowed for client portal.", title="Not Allowed")

	ensure_client_role(user_id)

	# Create session (passwordless)
	from frappe.auth import LoginManager
	lm = LoginManager()
	lm.user = user_id
	lm.post_login()
	frappe.session.user = user_id

	log_event("PORTAL_OTP_LOGIN", {
		"user": user_id,
		"client": client.name,
		"phone": phone_n,
		"ip": ip
	})

	return {
		"ok": True,
		"user": user_id,
		"client": client.name,
		"sid": frappe.session.sid,
	}

@frappe.whitelist()
def portal_logout():
	"""
	Logout for SPA
	"""
	if frappe.session.user == "Guest":
		return {"ok": True}
	frappe.local.login_manager.logout()
	return {"ok": True}
