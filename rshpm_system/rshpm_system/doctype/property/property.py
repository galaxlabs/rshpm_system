
import frappe
from frappe.model.document import Document
from frappe.utils import flt


LOCKED_STATUSES = {"Booked", "Allotted", "Possession", "Transferred"}

STATUS_FLOW = {
	"Inventory": {"Reserved", "Booked", "Blocked", "Cancelled"},
	"Reserved": {"Inventory", "Booked", "Blocked", "Cancelled"},
	"Booked": {"Allotted", "Blocked"},          # return to Inventory should come from Booking.cancel (manager-only)
	"Allotted": {"Possession", "Blocked"},
	"Possession": {"Transferred", "Blocked"},
	"Transferred": set(),
	"Cancelled": set(),
	"Blocked": {"Inventory"},  # optional: admin can unblock back to inventory
}


class Property(Document):
	def validate(self):
		self._normalize_fields()
		self._ensure_company()
		self._set_unique_id()
		self._compute_area_sqft()
		self._require_unique_fields()
		self._prevent_duplicates()
		self._enforce_status_flow()
		self._prevent_manual_current_booking_change()

	def _normalize_fields(self):
		def norm_spaces(v):
			v = (v or "").strip()
			return " ".join(v.split())

		if self.plot_number:
			self.plot_number = norm_spaces(self.plot_number)

		if self.housing_scheme:
			self.housing_scheme = norm_spaces(self.housing_scheme)

		if self.block:
			self.block = norm_spaces(self.block)

		if self.unit_type:
			self.unit_type = norm_spaces(self.unit_type)

	def _ensure_company(self):
		# important for portal + read_only field
		if self.company:
			return

		# try user default
		user_default_company = frappe.defaults.get_user_default("Company")
		if user_default_company:
			self.company = user_default_company
			return

		# fallback: first Company (single-company setups)
		self.company = frappe.db.get_value("Company", {}, "name")

	# def _set_unique_id(self):
	# 	company = (self.company or "").strip()
	# 	scheme = (self.housing_scheme or "").strip()
	# 	block = (self.block or "").strip()
	# 	plot = (self.plot_number or "").strip()
	# 	self.unique_id = f"{company}|{scheme}|{block}|{plot}"
	def _set_unique_id(self):
		self.unique_id = f"{self.company}|{self.housing_scheme}|{self.block}|{self.plot_number}|{self.unit_type}"  # reset first to avoid false positives in duplicate check
    # Must be stable and normalized (your _normalize_fields() already helps)
		parts = [
			self.company,
			self.housing_scheme,
			self.block,
			self.plot_number,
			self.unit_type,
		]
		# Safety: remove empties (shouldn't happen due to reqd fields, but safe)
		parts = [p for p in parts if p]
		self.unique_id = "|".join(parts)

	def _require_unique_fields(self):
		# Property should be a strict inventory unit; don't allow partial keys
		missing = []
		if not self.company: missing.append("Company")
		if not self.housing_scheme: missing.append("Housing Scheme")
		if not self.block: missing.append("Block")
		if not self.plot_number: missing.append("Plot Number")
		if not self.unit_type: missing.append("Unit Type")

		if missing:
			frappe.throw(
				"Missing required fields: " + ", ".join(missing),
				title="Incomplete Property",
			)

	def _prevent_duplicates(self):
		existing = frappe.db.exists(
			"Property",
			{"unique_id": self.unique_id, "name": ["!=", self.name]},
		)
		if existing:
			frappe.throw(
				f"Duplicate Property not allowed. Same key already exists: {existing}",
				title="Duplicate Property",
			)

	def _enforce_status_flow(self):
		if self.is_new():
			return

		old_status = frappe.db.get_value("Property", self.name, "status")
		if not old_status or self.status == old_status:
			return

		# Allowed manual roles
		allowed_roles = {"System Manager", "Admin", "Manager"}
		user_roles = set(frappe.get_roles())

		# Only Block / Unblock can be manual
		manual_allowed = (
			old_status in {"Inventory", "Reserved", "Blocked"}
			and self.status in {"Blocked", "Inventory"}
		)

		if not (user_roles & allowed_roles):
			frappe.throw(
				"Property status is controlled by Booking. "
				"Please use Booking to change availability.",
				title="Status Controlled",
			)

		if not manual_allowed:
			frappe.throw(
				f"Invalid manual status change: {old_status} → {self.status}. "
				"Use Booking workflow for this action.",
				title="Invalid Status Change",
			)

			if self.is_new():
				return

			old_status = frappe.db.get_value("Property", self.name, "status")
			if not old_status or self.status == old_status:
				return

			allowed = STATUS_FLOW.get(old_status, set())
			if self.status not in allowed:
				frappe.throw(
					f"Invalid status change: {old_status} → {self.status}. "
					"Use the correct workflow (Booking/Allotment/Possession/Transfer).",
					title="Invalid Status Change",
				)

	def _prevent_manual_current_booking_change(self):
		if self.is_new():
			return

		old = frappe.get_doc("Property", self.name)

		# once booked/allotted/etc, current_booking must not be changed manually
		if old.status in LOCKED_STATUSES:
			if self.current_booking != old.current_booking:
				frappe.throw(
					f"Property is already {old.status} under Booking {old.current_booking}. "
					"Manual change of Current Booking is not allowed.",
					title="Property Locked",
				)

	def _compute_area_sqft(self):
		area = flt(self.area_text)
		unit = (self.area_unit or "").strip()

		if area <= 0 or not unit:
			self.area_value = 0
			return

		MARLA_SQFT = 272.25
		KANAL_SQFT = 20 * MARLA_SQFT
		SQYD_SQFT = 9

		if unit == "Sqft":
			sqft = area
		elif unit == "Sqyd":
			sqft = area * SQYD_SQFT
		elif unit == "Marla":
			sqft = area * MARLA_SQFT
		elif unit == "Kanal":
			sqft = area * KANAL_SQFT
		else:
			sqft = area

		self.area_value = flt(sqft, 2)