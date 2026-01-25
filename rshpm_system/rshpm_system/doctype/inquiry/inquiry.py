# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

import re
import frappe
from frappe.model.document import Document

def _digits(value: str) -> str:
    return re.sub(r"\D+", "", value or "")

class Inquiry(Document):
    def validate(self):
        self._ensure_company()
        self._normalize()
        self._warn_possible_duplicates()

    def _ensure_company(self):
        if self.company:
            return
        user_default_company = frappe.defaults.get_user_default("Company")
        if user_default_company:
            self.company = user_default_company
            return
        self.company = frappe.db.get_value("Company", {}, "name")

    def _normalize(self):
        if self.full_name:
            self.full_name = self.full_name.strip()

        self.phone_normalized = _digits(self.phone)
        if len(self.phone_normalized) < 10:
            frappe.throw("Mobile number looks invalid. Please enter a valid phone number.", title="Invalid Mobile")

        self.cnic_normalized = _digits(self.cnic)
        # CNIC optional for Inquiry, so only validate if provided
        if self.cnic_normalized and len(self.cnic_normalized) != 13:
            frappe.throw("CNIC must be 13 digits (numbers only).", title="Invalid CNIC")

    def _warn_possible_duplicates(self):
        # Leads can duplicate; we warn (not block)
        if self.phone_normalized:
            existing = frappe.db.get_value(
                "Inquiry",
                {"phone_normalized": self.phone_normalized, "name": ["!=", self.name]},
                "name",
            )
            if existing:
                frappe.msgprint(
                    f"Possible duplicate Inquiry found with same mobile: {existing}",
                    alert=True,
                )

        if self.cnic_normalized:
            existing_client = frappe.db.get_value("Client", {"cnic_normalized": self.cnic_normalized}, "name")
            if existing_client:
                frappe.msgprint(
                    f"This CNIC already exists in Client: {existing_client}. You may want to link/convert to that client.",
                    alert=True,
                )

@frappe.whitelist()
def convert_to_client(inquiry_name: str) -> str:
    """Create or link a Client from this Inquiry. Returns Client name."""
    inq = frappe.get_doc("Inquiry", inquiry_name)
    inq.check_permission("write")

    # If already converted
    if inq.client:
        return inq.client

    # If CNIC exists in client, link it
    cnic_norm = _digits(inq.cnic)
    if cnic_norm and len(cnic_norm) == 13:
        existing_client = frappe.db.get_value("Client", {"cnic_normalized": cnic_norm}, "name")
        if existing_client:
            inq.client = existing_client
            inq.status = "Converted"
            inq.converted_on = frappe.utils.now()
            inq.converted_by = frappe.session.user
            inq.save(ignore_permissions=True)
            return existing_client

    # Otherwise create new client (requires CNIC!)
    if not cnic_norm or len(cnic_norm) != 13:
        frappe.throw("CNIC is required to convert Inquiry to Client.", title="CNIC Required")

    client = frappe.new_doc("Client")
    client.full_name = inq.full_name
    client.mobile_number = inq.phone
    client.cnic_number = inq.cnic
    client.address = inq.address
    client.company = inq.company
    client.kyc_status = "Unverified"
    client.insert()

    inq.client = client.name
    inq.status = "Converted"
    inq.converted_on = frappe.utils.now()
    inq.converted_by = frappe.session.user
    inq.save(ignore_permissions=True)

    return client.name
