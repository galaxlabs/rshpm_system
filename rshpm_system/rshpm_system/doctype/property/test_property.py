# Copyright (c) 2026, Galaxy Labs and Contributors
# See license.txt

# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

import io
import json
import uuid

import frappe
from frappe.model.document import Document
from frappe.utils import nowdate
from frappe.utils.file_manager import save_file

# QR lib: install with
# bench pip install qrcode[pil]
import qrcode
from qrcode.constants import ERROR_CORRECT_M


def _has_column(doctype: str, fieldname: str) -> bool:
	"""Safe check for optional schema fields."""
	try:
		return bool(frappe.db.has_column(f"tab{doctype}", fieldname))
	except Exception:
		return False


def _ensure_default_status(doc: Document):
	"""Make sure status is set to a stable default."""
	# You should also define options in the DocType:
	# Available / Booked / Sold / Blocked
	if not doc.status:
		doc.status = "Available"


def _make_uuid() -> str:
	return str(uuid.uuid4())


def _qr_png_bytes(payload_text: str) -> bytes:
	"""
	Generate a QR PNG as bytes.
	payload_text should be compact (URL or JSON string).
	"""
	qr = qrcode.QRCode(
		version=None,
		error_correction=ERROR_CORRECT_M,
		box_size=10,
		border=4,
	)
	qr.add_data(payload_text)
	qr.make(fit=True)

	img = qr.make_image(fill_color="black", back_color="white")

	buf = io.BytesIO()
	img.save(buf, format="PNG")
	return buf.getvalue()


def _build_qr_payload_dict(property_doc: Document, holder_name: str = None,
						   booking_name: str = None, token_amount=None,
						   token_date: str = None) -> dict:
	"""
	Create structured data that you can later decode from QR.
	You can add/remove fields anytime without changing QR generation logic.
	"""
	data = {
		"type": "property_booking_qr",
		"property": property_doc.name,
		"plot_number": property_doc.plot_number,
		"block": property_doc.block,
		"property_type": getattr(property_doc, "property_type", None),
		"total_cost": getattr(property_doc, "total_cost", None),
		"unique_id": property_doc.unique_id,
		"status": property_doc.status,
		"holder_name": holder_name,     # Client / property holder
		"booking": booking_name,        # Booking reference
		"token_amount": token_amount,   # Token paid amount
		"token_date": token_date,       # Token paid date
		"generated_on": nowdate(),
	}

	# Remove null keys for cleaner payload
	return {k: v for k, v in data.items() if v not in (None, "", [])}


def generate_and_attach_property_qr(property_name: str,
								   holder_name: str = None,
								   booking_name: str = None,
								   token_amount=None,
								   token_date: str = None,
								   force: int = 0) -> str:
	"""
	Generate QR for a property and attach it in Property.qr_code.
	Returns File URL or File name.

	This is written as a function so you can call it later from:
	- Booking on_submit
	- Payment on_submit (token)
	- a custom button (Regenerate QR) (later)
	"""
	prop = frappe.get_doc("Property", property_name)

	# Ensure UUID exists
	if not prop.unique_id:
		prop.unique_id = _make_uuid()
		prop.db_set("unique_id", prop.unique_id, update_modified=False)

	# If QR already exists and not forcing, skip
	if getattr(prop, "qr_code", None) and not int(force):
		return prop.qr_code

	payload_dict = _build_qr_payload_dict(
		prop,
		holder_name=holder_name,
		booking_name=booking_name,
		token_amount=token_amount,
		token_date=token_date,
	)

	# QR payload as JSON (compact)
	payload_text = json.dumps(payload_dict, separators=(",", ":"), ensure_ascii=False)

	png = _qr_png_bytes(payload_text)

	filename = f"PROPERTY-QR-{prop.name}.png"
	file_doc = save_file(
		fname=filename,
		content=png,
		dt="Property",
		dn=prop.name,
		is_private=0
	)

	# Attach to qr_code field (your fieldname is qr_code)
	if _has_column("Property", "qr_code"):
		prop.db_set("qr_code", file_doc.file_url, update_modified=False)

	return file_doc.file_url


def lock_property_for_booking(property_name: str):
	"""
	Transaction-safe lock to avoid double booking.
	Call this inside Booking validate/submit before confirming availability.

	This uses SELECT ... FOR UPDATE so only one request can proceed at a time.
	"""
	# Important: This works on MariaDB/MySQL InnoDB
	row = frappe.db.sql(
		"""
		SELECT name, status
		FROM `tabProperty`
		WHERE name=%s
		FOR UPDATE
		""",
		(property_name,),
		as_dict=True
	)

	if not row:
		frappe.throw(f"Property '{property_name}' not found.")

	status = row[0].get("status")
	if status and status != "Available":
		frappe.throw(f"Property {property_name} is already {status}.")

	# If status is empty, treat as not-safe; enforce default
	# (Better to fix status options + default in DocType)
	return True


class Property(Document):
	def before_insert(self):
		# Auto-generate UUID into unique_id once
		if not self.unique_id:
			self.unique_id = _make_uuid()

	def validate(self):
		_ensure_default_status(self)

		# Optional: avoid duplicate amenities in the table
		if self.get("amenities"):
			seen = set()
			for row in self.amenities:
				if row.amenity in seen:
					frappe.throw(f"Duplicate amenity found: {row.amenity}")
				seen.add(row.amenity)

	def on_update(self):
		"""
	Optional auto behavior (safe default):
	- If property becomes Booked and qr_code is empty, generate QR.
	But we do NOT know your exact 'token paid' logic yet,
	so we keep it conservative: generate only when status is Booked AND qr is empty.
	"""
		try:
			if self.status == "Booked" and not getattr(self, "qr_code", None):
				# We don't know holder/token info at Property level yet,
				# so just generate basic QR now (UUID + plot + block etc.)
				generate_and_attach_property_qr(self.name)
		except Exception:
			# Never block save if QR generation fails (optional feature)
			frappe.log_error(frappe.get_traceback(), "Property QR generation failed")
