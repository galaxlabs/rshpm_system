# Copyright (c) 2026, Galaxy Labs
# For license information, please see license.txt

# Copyright (c) 2026, Galaxy Labs
# For license information, please see license.txt

import re
import frappe
from frappe.model.document import Document


def _digits(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


def _make_user_id(email_address: str, phone_normalized: str) -> str:
    """
    Deterministic user id:
    - if email exists -> use email
    - else -> phone@client.local
    """
    email = (email_address or "").strip().lower()
    if email:
        return email
    return f"{phone_normalized}@client.local"


def _has_role(user_id: str, role: str) -> bool:
    try:
        return role in (frappe.get_roles(user_id) or [])
    except Exception:
        return False


class Client(Document):
    def validate(self):
        self._ensure_company()
        self._normalize()
        self._prevent_duplicates()

    def after_insert(self):
        self._ensure_system_user_and_permissions()

    def on_update(self):
        # on every update, ensure user exists and is correct (handles email change)
        self._ensure_system_user_and_permissions()

    def on_trash(self):
        self._delete_linked_user_if_safe()

    # ---------------------------
    # Core validations
    # ---------------------------

    def _ensure_company(self):
        if self.company:
            return

        user_default_company = frappe.defaults.get_user_default("Company")
        if user_default_company:
            self.company = user_default_company
            return

        self.company = frappe.db.get_value("Company", {}, "name")

    def _normalize(self):
        # CNIC: digits only, must be 13
        self.cnic_normalized = _digits(self.cnic_number)
        if len(self.cnic_normalized) != 13:
            frappe.throw("CNIC must be 13 digits (numbers only).", title="Invalid CNIC")

        # Phone: digits only, min 10
        self.phone_normalized = _digits(self.mobile_number)
        if len(self.phone_normalized) < 10:
            frappe.throw("Mobile number looks invalid. Please enter a valid phone number.", title="Invalid Mobile")

        # Trim
        if self.full_name:
            self.full_name = self.full_name.strip()
        if self.father_name:
            self.father_name = self.father_name.strip()

    def _prevent_duplicates(self):
        # CNIC unique
        existing_cnic = frappe.db.exists(
            "Client",
            {"cnic_normalized": self.cnic_normalized, "name": ["!=", self.name]},
        )
        if existing_cnic:
            frappe.throw(
                f"Client already exists with this CNIC. Existing Client: {existing_cnic}",
                title="Duplicate CNIC",
            )

        # Phone unique
        existing_phone = frappe.db.exists(
            "Client",
            {"phone_normalized": self.phone_normalized, "name": ["!=", self.name]},
        )
        if existing_phone:
            frappe.throw(
                f"Client already exists with this mobile number. Existing Client: {existing_phone}",
                title="Duplicate Mobile",
            )

    # ---------------------------
    # User + permissions
    # ---------------------------

    def _ensure_system_user_and_permissions(self):
        # Ensure field exists in DocType (no DB table misuse)
        if not frappe.get_meta("Client").has_field("user"):
            frappe.throw("Client DocType must have field `user` (Link to User, unique).")

        self._ensure_role_exists()

        desired_user_id = _make_user_id(getattr(self, "email_address", None), self.phone_normalized)

        current_user_id = (self.user or "").strip().lower()

        # Case 1: client has a linked user
        if current_user_id and frappe.db.exists("User", current_user_id):
            # If email changed (desired user differs), we will switch link to desired user
            if current_user_id != desired_user_id:
                # Only auto-switch if current user looks like a managed client user (System User + Client role)
                user_type = frappe.get_cached_value("User", current_user_id, "user_type")
                if user_type == "System User" and _has_role(current_user_id, "Client"):
                    # Ensure desired exists, then re-link
                    self._create_user_if_missing(desired_user_id)
                    self._assign_client_role(desired_user_id)
                    self._ensure_user_disabled(desired_user_id)
                    self._ensure_user_permission(desired_user_id)

                    # Re-link client to new user
                    self.db_set("user", desired_user_id, update_modified=False)

                    # Optionally delete old user if safe
                    self._delete_user_if_safe(current_user_id, except_client=self.name)

                    return

            # If linked user is already correct, just enforce role/permission/disabled
            self._assign_client_role(current_user_id)
            self._ensure_user_disabled(current_user_id)
            self._ensure_user_permission(current_user_id)
            return

        # Case 2: no linked user OR linked user missing -> create/link desired
        self._create_user_if_missing(desired_user_id)
        self._assign_client_role(desired_user_id)
        self._ensure_user_disabled(desired_user_id)
        self._ensure_user_permission(desired_user_id)
        self.db_set("user", desired_user_id, update_modified=False)

    def _ensure_role_exists(self):
        if not frappe.db.exists("Role", "Client"):
            frappe.get_doc({"doctype": "Role", "role_name": "Client"}).insert(ignore_permissions=True)

    def _create_user_if_missing(self, user_id: str):
        if frappe.db.exists("User", user_id):
            return

        u = frappe.get_doc({
            "doctype": "User",
            "email": user_id,
            "first_name": (self.full_name or "Client")[:140],
            "enabled": 0,               # keep disabled
            "send_welcome_email": 0,
            "user_type": "System User",
            "roles": [{"role": "Client"}],
            "module_profile": "client"
        })
        u.insert(ignore_permissions=True)

    def _assign_client_role(self, user_id: str):
        u = frappe.get_doc("User", user_id)
        u.add_roles("Client")
        u.save(ignore_permissions=True)

    def _ensure_user_disabled(self, user_id: str):
        # Always keep disabled (as requested)
        frappe.db.set_value("User", user_id, "enabled", 0, update_modified=False)

    def _ensure_user_permission(self, user_id: str):
        # Restrict user to only this Client
        exists = frappe.db.exists("User Permission", {
            "user": user_id,
            "allow": "Client",
            "for_value": self.name
        })
        if not exists:
            frappe.get_doc({
                "doctype": "User Permission",
                "user": user_id,
                "allow": "Client",
                "for_value": self.name,
                "apply_to_all_doctypes": 0
            }).insert(ignore_permissions=True)

    # ---------------------------
    # Delete linked user (safe)
    # ---------------------------

    def _delete_linked_user_if_safe(self):
        user_id = (self.user or "").strip().lower()
        if not user_id or user_id in ("administrator", "guest"):
            return
        self._delete_user_if_safe(user_id, except_client=self.name)

    def _delete_user_if_safe(self, user_id: str, except_client: str = None):
        """
        Delete user only if safe:
        - exists
        - System User
        - has Client role
        - NOT linked to any other Client
        """
        if not frappe.db.exists("User", user_id):
            return

        # linked to another client? don't delete
        filters = {"user": user_id}
        if except_client:
            filters["name"] = ["!=", except_client]
        other = frappe.db.get_value("Client", filters, "name")
        if other:
            return

        user_type = frappe.get_cached_value("User", user_id, "user_type")
        if user_type != "System User":
            return

        if not _has_role(user_id, "Client"):
            return

        # remove user permissions for that user (optional; user will be deleted anyway)
        frappe.db.delete("User Permission", {"user": user_id})

        # delete user
        frappe.delete_doc("User", user_id, ignore_permissions=True, force=True)
