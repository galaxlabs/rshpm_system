import frappe
from frappe.utils import now_datetime

def expire_reserved_properties(limit: int = 500):
    """
    Reverts expired Reserved properties back to Inventory.
    Safe: does NOT touch Booked/Allotted/etc.
    """
    now = now_datetime()

    # Only those which are reserved and expired
    props = frappe.get_all(
        "Property",
        filters={
            "status": "Reserved",
            "reserved_till": ["<", now],
        },
        fields=["name", "current_booking"],
        limit=limit,
    )

    for p in props:
        # If there is a current_booking, verify whether it is still a valid submitted Booking
        if p.current_booking:
            booking_status = frappe.db.get_value("Booking", p.current_booking, "docstatus")
            # docstatus 1 means submitted; treat as active lock
            if booking_status == 1:
                continue

        # Revert to inventory
        frappe.db.set_value(
            "Property",
            p.name,
            {
                "status": "Inventory",
                "reserved_till": None,
                "current_booking": None,
                "booked_by": "",
            },
            update_modified=False,
        )

    frappe.db.commit()
