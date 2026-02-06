// Copyright (c) 2026, Galaxy Labs and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Property", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on("Property", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Bookings"), () => {
			frappe.set_route("List", "Booking", { property: frm.doc.name });
		});

		frm.add_custom_button(__("Payments"), () => {
			frappe.set_route("List", "Payment", { property: frm.doc.name });
		});
	}
});
