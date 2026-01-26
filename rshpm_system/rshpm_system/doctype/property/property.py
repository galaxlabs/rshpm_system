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
		if self.plot_number:
			self.plot_number = str(self.plot_number).strip()

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

	def _set_unique_id(self):
		company = (self.company or "").strip()
		scheme = (self.housing_scheme or "").strip()
		block = (self.block or "").strip()
		plot = (self.plot_number or "").strip()
		self.unique_id = f"{company}|{scheme}|{block}|{plot}"

	def _require_unique_fields(self):
		# Property should be a strict inventory unit; don't allow partial keys
		missing = []
		if not self.company: missing.append("Company")
		if not self.housing_scheme: missing.append("Housing Scheme")
		if not self.block: missing.append("Block")
		if not self.plot_number: missing.append("Plot Number")

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