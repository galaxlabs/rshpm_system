import frappe

def execute():
    # Make columns NOT NULL so uniqueness is enforced consistently
    frappe.db.sql("""
        ALTER TABLE `tabClient`
        MODIFY `cnic_normalized` varchar(255) NOT NULL,
        MODIFY `phone_normalized` varchar(255) NOT NULL
    """)

    # Add unique indexes if not exist
    def add_index(index_name, column):
        exists = frappe.db.sql(
            "SHOW INDEX FROM `tabClient` WHERE Key_name=%s",
            (index_name,),
        )
        if exists:
            return
        frappe.db.sql(f"""
            ALTER TABLE `tabClient`
            ADD UNIQUE INDEX `{index_name}` (`{column}`)
        """)

    add_index("unique_client_cnic_normalized", "cnic_normalized")
    add_index("unique_client_phone_normalized", "phone_normalized")
