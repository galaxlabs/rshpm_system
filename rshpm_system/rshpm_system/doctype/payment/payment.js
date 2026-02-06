// Copyright (c) 2026, Galaxy Labs and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Payment", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on("Payment", {
	booking(frm) {
		if (!frm.doc.booking) return;

		frappe.db.get_value("Booking", frm.doc.booking, ["payment_mode"])
			.then(r => {
				const pm = (r.message && r.message.payment_mode) || "";
				if (pm === "Installments") {
					frm.set_value("payment_type", "Installment");
				}
			});
	}
});

