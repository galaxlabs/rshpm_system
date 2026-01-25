// Copyright (c) 2026, Galaxy Labs and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Inquiry", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on("Inquiry", {
  refresh(frm) {
    if (!frm.is_new() && frm.doc.status !== "Converted") {
      frm.add_custom_button("Convert to Client", async () => {
        const r = await frappe.call({
          method: "rshpm_system.rshpm_system.doctype.inquiry.inquiry.convert_to_client",
          args: { inquiry_name: frm.doc.name },
        });
        if (r.message) {
          frm.set_value("client", r.message);
          frm.set_value("status", "Converted");
          frm.reload_doc();
          frappe.set_route("Form", "Client", r.message);
        }
      });
    }
  },
});

