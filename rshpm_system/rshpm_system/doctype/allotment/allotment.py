import frappe
from frappe.model.document import Document

LOCKED_PROPERTY_STATUSES = {"Possession", "Transferred"}

class Allotment(Document):
    def validate(self):
        self._ensure_company()
        self._validate_booking_property()

    def on_submit(self):
        self._apply_allotment()

    def on_cancel(self):
        self._revert_allotment()

    def _ensure_company(self):
        if self.company:
            return
        user_default_company = frappe.defaults.get_user_default("Company")
        if user_default_company:
            self.company = user_default_company
            return
        self.company = frappe.db.get_value("Company", {}, "name")

    def _validate_booking_property(self):
        if not self.booking:
            return

        booking = frappe.db.get_value(
            "Booking",
            self.booking,
            ["docstatus", "property", "customer", "company", "status"],
            as_dict=True
        )
        if not booking:
            frappe.throw("Invalid Booking.", title="Invalid Booking")

        if booking.docstatus != 1:
            frappe.throw("Booking must be submitted before allotment.", title="Booking Not Submitted")

        # Keep company consistent
        if booking.company and self.company and booking.company != self.company:
            frappe.throw("Booking company and Allotment company must be the same.", title="Company Mismatch")

        # set from booking
        self.property = booking.property
        self.client = booking.customer

        prop = frappe.db.get_value(
            "Property",
            self.property,
            ["status", "current_booking"],
            as_dict=True
        )
        if not prop:
            frappe.throw("Invalid Property.", title="Invalid Property")

        # Must be locked under this booking
        if (prop.current_booking or "") != self.booking:
            frappe.throw(
                f"Property is not locked under this Booking. Current Booking: {prop.current_booking}",
                title="Property Not Locked"
            )

        # Must be in Booked state (or already Allotted if re-saving)
        if prop.status not in {"Booked", "Allotted"}:
            frappe.throw(
                f"Property must be Booked before Allotment. Current status: {prop.status}",
                title="Invalid Property Status"
            )

    def _apply_allotment(self):
        # Double safety: do not allot if already in later states
        prop_status = frappe.db.get_value("Property", self.property, "status")
        if prop_status in LOCKED_PROPERTY_STATUSES:
            frappe.throw(f"Cannot allot property because it is already {prop_status}.", title="Property Locked")

        # Property becomes Allotted
        frappe.db.set_value("Property", self.property, {
            "status": "Allotted",
            "is_owner_locked": 1,
        })

        # Booking status becomes Allotted (your Booking has this option)
        frappe.db.set_value("Booking", self.booking, "status", "Allotted")

        # If you kept Allotment.status field, sync it
        if hasattr(self, "status") and self.status != "Submitted":
            self.db_set("status", "Submitted")

    def _revert_allotment(self):
        # Prevent cancel if property already progressed further
        prop_status = frappe.db.get_value("Property", self.property, "status")
        if prop_status in LOCKED_PROPERTY_STATUSES:
            frappe.throw(f"Cannot cancel allotment because property is already {prop_status}.", title="Not Allowed")

        # revert to Booked
        frappe.db.set_value("Property", self.property, {"status": "Booked"})

        # revert booking status to Submitted (or Active if you prefer)
        frappe.db.set_value("Booking", self.booking, "status", "Submitted")

        if hasattr(self, "status") and self.status != "Cancelled":
            self.db_set("status", "Cancelled")
