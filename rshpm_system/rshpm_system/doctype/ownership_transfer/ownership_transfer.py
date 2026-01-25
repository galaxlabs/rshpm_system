# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import nowdate

LOCKED_STATUSES = {"Blocked", "Cancelled"}

class OwnershipTransfer(Document):
	def validate(self):
		self._ensure_company()
		self._validate_parties()
		self._validate_property_owner()

	def on_submit(self):
		self._apply_transfer()

	def on_cancel(self):
		self._revert_transfer()

	def _ensure_company(self):
		if self.company:
			return
		user_default_company = frappe.defaults.get_user_default("Company")
		if user_default_company:
			self.company = user_default_company
			return
		self.company = frappe.db.get_value("Company", {}, "name")

	def _validate_parties(self):
		if not self.property:
			frappe.throw("Property is required.", title="Missing Property")
		if not self.from_client:
			frappe.throw("From Client is required.", title="Missing From Client")
		if not self.to_client:
			frappe.throw("To Client is required.", title="Missing To Client")
		if self.from_client == self.to_client:
			frappe.throw("From Client and To Client cannot be the same.", title="Invalid Transfer")
		if not self.transfer_date:
			self.transfer_date = nowdate()

	def _validate_property_owner(self):
		prop = frappe.db.get_value(
			"Property",
			self.property,
			["status", "current_booking", "current_owner"],
			as_dict=True
		)
		if not prop:
			frappe.throw("Invalid Property.", title="Invalid Property")

		status = (prop.status or "").strip()

		if status in LOCKED_STATUSES:
			frappe.throw(f"Cannot transfer because property is {status}.", title="Not Allowed")

		# Policy: transfer allowed only after possession
		if status not in ("Possession", "Transferred"):
			frappe.throw(
				f"Transfer is allowed only after Possession. Current property status: {status}",
				title="Not Allowed"
			)

		# Possession must have created an ownership record
		if not prop.current_owner:
			frappe.throw(
				"Property has no current owner record. Please submit Possession first (it creates ownership).",
				title="Missing Ownership"
			)

		own = frappe.db.get_value(
			"Property Ownership",
			prop.current_owner,
			["owner_client", "ownership_status"],
			as_dict=True
		)
		if not own:
			frappe.throw("Current owner record is missing/corrupted.", title="Missing Ownership")

		if (own.ownership_status or "") != "Active":
			frappe.throw("Current owner record is not Active. Fix ownership before transfer.", title="Invalid Ownership")

		if own.owner_client and own.owner_client != self.from_client:
			frappe.throw(
				f"From Client must match current owner ({own.owner_client}).",
				title="Owner Mismatch"
			)

	def _apply_transfer(self):
		# Must still have current_owner at submit time
		old_own_name = frappe.db.get_value("Property", self.property, "current_owner")
		if not old_own_name:
			frappe.throw("Property has no current owner. Submit Possession first.", title="Missing Ownership")

		# close old ownership
		old = frappe.get_doc("Property Ownership", old_own_name)
		old.ownership_status = "Closed"
		old.ownership_end_date = nowdate()
		old.save(ignore_permissions=True)

		# create new ownership
		new = frappe.new_doc("Property Ownership")
		new.property = self.property
		new.owner_client = self.to_client
		new.ownership_status = "Active"
		new.ownership_start_date = nowdate()
		new.booking = getattr(self, "booking", None)
		new.remarks = f"Transferred from {self.from_client} via {self.name}"
		new.insert(ignore_permissions=True)

		# update property
		frappe.db.set_value("Property", self.property, {
			"current_owner": new.name,
			"status": "Transferred",
			"is_owner_locked": 1,
		})

		# optional: sync custom status
		if hasattr(self, "status") and self.status != "Completed":
			self.db_set("status", "Completed")

	def _revert_transfer(self):
		prop_status = frappe.db.get_value("Property", self.property, "status")
		if prop_status != "Transferred":
			return

		# close current active ownership
		current_owner = frappe.db.get_value("Property", self.property, "current_owner")
		if current_owner:
			cur = frappe.get_doc("Property Ownership", current_owner)
			cur.ownership_status = "Closed"
			cur.ownership_end_date = nowdate()
			cur.save(ignore_permissions=True)

		# create ownership back to from_client
		back = frappe.new_doc("Property Ownership")
		back.property = self.property
		back.owner_client = self.from_client
		back.ownership_status = "Active"
		back.ownership_start_date = nowdate()
		back.remarks = f"Transfer cancelled {self.name} (reverted owner)"
		back.insert(ignore_permissions=True)

		frappe.db.set_value("Property", self.property, {
			"current_owner": back.name,
			"status": "Possession",  # keep as Possession per your policy
			"is_owner_locked": 1,
		})

		if hasattr(self, "status") and self.status != "Cancelled":
			self.db_set("status", "Cancelled")
