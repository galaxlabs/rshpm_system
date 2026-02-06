// Copyright (c) 2026, Galaxy Labs and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Booking", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on("Booking", {
	refresh(frm) {
		// Only after saved
		if (frm.is_new() || frm.doc.docstatus !== 1) return;
        
		frm.add_custom_button(__("Make Payment"), () => {
			frappe.new_doc("Payment", {
				booking: frm.doc.name,
				customer: frm.doc.customer,
				property: frm.doc.property,
				company: frm.doc.company,
				posting_date: frappe.datetime.get_today(),
			});
		});

		frm.add_custom_button(__("Ledger"), () => {
			frappe.set_route("List", "Payment", {
				booking: frm.doc.name,
			});
		});
	}
});
