# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

def _abbr(name: str) -> str:
	# "Galaxy Labs" -> "GL"
	parts = [p for p in (name or "").strip().split() if p]
	if not parts:
		return ""
	if len(parts) == 1:
		return parts[0][:4].upper()
	return ("".join(p[0] for p in parts[:4])).upper()

class PropertyCompany(Document):
	def validate(self):
		if not self.company_code:
			code = _abbr(self.company_name)
			if not code:
				frappe.throw("Company Name is required to generate Company Code.")

			# ensure uniqueness (GL, GL-2, GL-3...)
			base = code
			i = 1
			while frappe.db.exists("Property Company", {"company_code": code, "name": ["!=", self.name]}):
				i += 1
				code = f"{base}{i}"

			self.company_code = code
