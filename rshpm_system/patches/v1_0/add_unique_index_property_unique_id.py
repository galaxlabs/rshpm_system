import frappe

def execute():
    index = "unique_property_unique_id"

    exists = frappe.db.sql("""
        SHOW INDEX FROM `tabProperty` WHERE Key_name=%s
    """, (index,))

    if exists:
        return

    frappe.db.sql("""
        ALTER TABLE `tabProperty`
        ADD UNIQUE INDEX `unique_property_unique_id` (`unique_id`)
    """)
