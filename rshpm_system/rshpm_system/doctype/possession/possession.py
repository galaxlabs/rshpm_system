# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import nowdate

class Possession(Document):
	def validate(self):
		self._ensure_company()
		self._validate_booking()

	def on_submit(self):
		self._apply_possession_and_set_owner()

	def on_cancel(self):
		self._revert_possession()

	def _ensure_company(self):
		if self.company:
			return
		user_default_company = frappe.defaults.get_user_default("Company")
		if user_default_company:
			self.company = user_default_company
			return
		self.company = frappe.db.get_value("Company", {}, "name")

	def _validate_booking(self):
		if not self.booking:
			frappe.throw("Booking is required.", title="Missing Booking")

		b = frappe.db.get_value(
			"Booking",
			self.booking,
			["docstatus", "property", "customer", "company", "status", "remaining_balance"],
			as_dict=True,
		)
		if not b:
			frappe.throw("Invalid Booking.", title="Invalid Booking")

		if b.docstatus != 1:
			frappe.throw("Booking must be submitted before Possession.", title="Booking Not Submitted")

		if b.company and self.company and b.company != self.company:
			frappe.throw("Company mismatch between Booking and Possession.", title="Company Mismatch")

		# set links from booking
		self.property = b.property
		self.client = b.customer

		# must be allotted/active before possession
		if (b.status or "") not in ("Allotted", "Active", "Completed"):
			frappe.throw(
				f"Booking must be Allotted/Active before Possession. Current: {b.status}",
				title="Invalid Booking Status"
			)

		# Optional: enforce clearance checkbox
		if not getattr(self, "final_clearance_check", 0):
			frappe.throw("Final Clearance Check must be checked before submitting Possession.", title="Clearance Required")

		# Optional: enforce full payment
		try:
			if b.remaining_balance is not None and float(b.remaining_balance) > 0:
				frappe.throw("Remaining balance exists. Clear all dues before possession.", title="Payment Pending")
		except Exception:
			pass

	def _apply_possession_and_set_owner(self):
		# Update property status
		frappe.db.set_value("Property", self.property, {
			"status": "Possession",
			"is_owner_locked": 1,
		})

		# Close any existing active ownership (if any)
		current_owner = frappe.db.get_value("Property", self.property, "current_owner")
		if current_owner:
			own = frappe.get_doc("Property Ownership", current_owner)
			# only close if active
			if (own.ownership_status or "") == "Active":
				own.ownership_status = "Closed"
				own.ownership_end_date = nowdate()
				own.save(ignore_permissions=True)

		# Create new ownership for the client
		new_own = frappe.new_doc("Property Ownership")
		new_own.property = self.property
		new_own.owner_client = self.client
		new_own.ownership_status = "Active"
		new_own.ownership_start_date = self.possession_date or nowdate()
		new_own.booking = self.booking
		new_own.remarks = f"Ownership created on Possession {self.name}"
		new_own.insert(ignore_permissions=True)

		# Point property to latest ownership
		frappe.db.set_value("Property", self.property, {
			"current_owner": new_own.name,
		})

		# Booking becomes Completed
		frappe.db.set_value("Booking", self.booking, "status", "Completed")

	def _revert_possession(self):
		"""
	Conservative cancel policy:
	- Allowed only if property hasn't been transferred.
	- We do NOT delete ownership history; we close the ownership created by this possession if linked.
	"""
		prop_status = frappe.db.get_value("Property", self.property, "status")
		if prop_status == "Transferred":
			frappe.throw("Cannot cancel possession after transfer.", title="Not Allowed")

		# Close current ownership (created on possession) if it matches booking+client+property
		current_owner = frappe.db.get_value("Property", self.property, "current_owner")
		if current_owner:
			own = frappe.get_doc("Property Ownership", current_owner)
			if own.property == self.property and own.booking == self.booking and own.owner_client == self.client:
				own.ownership_status = "Closed"
				own.ownership_end_date = nowdate()
				own.save(ignore_permissions=True)

		# Revert property status to Allotted (or Booked if you want stricter)
		frappe.db.set_value("Property", self.property, {"status": "Allotted"})

		# Booking status back to Allotted
		frappe.db.set_value("Booking", self.booking, "status", "Allotted")
