# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

import re
import frappe
from frappe.model.document import Document

def _digits(value: str) -> str:
    return re.sub(r"\D+", "", value or "")

class Client(Document):
    def validate(self):
        self._ensure_company()
        self._normalize()
        self._prevent_duplicates()

    def _ensure_company(self):
        if self.company:
            return
        user_default_company = frappe.defaults.get_user_default("Company")
        if user_default_company:
            self.company = user_default_company
            return
        self.company = frappe.db.get_value("Company", {}, "name")

    def _normalize(self):
        # CNIC: store digits only (13 digits for PK CNIC)
        self.cnic_normalized = _digits(self.cnic_number)
        if len(self.cnic_normalized) != 13:
            frappe.throw("CNIC must be 13 digits (numbers only).", title="Invalid CNIC")

        # Phone: digits only (you can later enforce PK formats)
        self.phone_normalized = _digits(self.mobile_number)
        if len(self.phone_normalized) < 10:
            frappe.throw("Mobile number looks invalid. Please enter a valid phone number.", title="Invalid Mobile")

        # Trim names
        if self.full_name:
            self.full_name = self.full_name.strip()
        if self.father_name:
            self.father_name = self.father_name.strip()

    def _prevent_duplicates(self):
        # CNIC must be unique (hard rule)
        existing_cnic = frappe.db.exists(
            "Client",
            {"cnic_normalized": self.cnic_normalized, "name": ["!=", self.name]},
        )
        if existing_cnic:
            frappe.throw(
                f"Client already exists with this CNIC. Existing Client: {existing_cnic}",
                title="Duplicate CNIC",
            )

        # Phone must be unique (recommended)
        existing_phone = frappe.db.exists(
            "Client",
            {"phone_normalized": self.phone_normalized, "name": ["!=", self.name]},
        )
        if existing_phone:
            frappe.throw(
                f"Client already exists with this mobile number. Existing Client: {existing_phone}",
                title="Duplicate Mobile",
            )
