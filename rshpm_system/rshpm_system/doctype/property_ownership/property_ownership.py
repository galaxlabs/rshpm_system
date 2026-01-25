# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import nowdate

class PropertyOwnership(Document):
	def validate(self):
		self._validate_unique_property()
		self._normalize_status()

	def _validate_unique_property(self):
		if not self.property:
			frappe.throw("Property is required.", title="Missing Property")

		existing = frappe.db.exists("Property Ownership", {"property": self.property, "name": ["!=", self.name]})
		if existing:
			frappe.throw(f"Ownership already exists for this Property: {existing}", title="Duplicate Ownership")

	def _normalize_status(self):
		if not self.ownership_status:
			self.ownership_status = "Active"

		if self.ownership_status not in ("Active", "Closed"):
			frappe.throw("Ownership Status must be Active or Closed.", title="Invalid Ownership Status")

		if self.ownership_status == "Active" and self.ownership_end_date:
			self.ownership_end_date = None

		if self.ownership_status == "Closed" and not self.ownership_end_date:
			self.ownership_end_date = nowdate()
