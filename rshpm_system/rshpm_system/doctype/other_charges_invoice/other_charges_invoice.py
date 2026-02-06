# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

# Copyright (c) 2026, Galaxy Labs and Contributors
# See license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate


def _sum_items(items) -> float:
	total = 0.0
	for r in (items or []):
		total += flt(r.amount)
	return total


def _has_any_installment_payment(booking, precision=2) -> bool:
	for r in (booking.installment_schedule or []):
		if flt(r.paid_amount, precision) > 0:
			return True
	return False


def _find_next_pending_row(booking, precision=2):
	"""Earliest row that still has pending amount."""
	for r in (booking.installment_schedule or []):
		if flt(r.due_amount, precision) - flt(r.paid_amount, precision) > 0:
			return r
	return None


class OtherChargesInvoice(Document):
	def validate(self):
		if not self.booking:
			frappe.throw("Booking is required.")

		if not self.items:
			frappe.throw("Please add at least one item.")

		for r in self.items:
			if flt(r.amount) <= 0:
				frappe.throw("Item amount must be greater than 0.")

		self.total_amount = _sum_items(self.items)

		# Allocation rule validation
		if (self.allocation_method or "") == "Create New Installment" and not self.effective_from:
			frappe.throw("Effective From is required when Allocation Method is 'Create New Installment'.")

	def on_submit(self):
		self._apply_to_booking(is_cancel=False)

	def on_cancel(self):
		self._apply_to_booking(is_cancel=True)

	# ----------------------------
	# Core: apply/cancel impact
	# ----------------------------
	def _apply_to_booking(self, is_cancel: bool):
		"""
		We do NOT call Booking.validate() (it may block due to Property status).
		Instead we call Booking internal recalculation methods and save with ignore_validate.
		"""
		booking = frappe.get_doc("Booking", self.booking)

		# if booking cancelled, block changes
		if (booking.status or "") == "Cancelled":
			frappe.throw("Cannot apply charges because Booking is Cancelled.")

		precision = frappe.get_precision("Booking", "net_total") or 2

		# For cancel, we try to reverse special allocation changes safely (only if still unpaid)
		if is_cancel and (booking.payment_mode or "") == "Installments":
			self._try_reverse_special_allocation(booking, precision)

		# Recompute Booking rollups from submitted invoices (this invoice docstatus already changed here)
		booking._pull_other_charges_total()
		booking._recalculate_totals()

		# Schedule impact only if Installments
		if (booking.payment_mode or "") == "Installments":
			paid_any = _has_any_installment_payment(booking, precision)

			# If nothing paid, safest is regenerate full schedule so amounts match new totals.
			if not paid_any:
				booking._generate_installment_schedule()
			else:
				# If something paid, protect paid rows and adjust only unpaid rows.
				# But first apply "special" methods (Add to Next / Create New) on submit only
				if not is_cancel:
					self._apply_special_allocation(booking, precision)

				# Then redistribute (equalize) unpaid rows to match latest totals
				booking._redistribute_unpaid_rows_if_needed()

			booking._refresh_due_metrics()

		booking._auto_set_status()

		# Save without running Booking.validate()
		booking.flags.ignore_validate = True
		booking.flags.ignore_mandatory = True
		booking.save(ignore_permissions=True)

	# ----------------------------
	# Allocation methods
	# ----------------------------
	def _apply_special_allocation(self, booking, precision):
		method = (self.allocation_method or "").strip()
		if method == "Spread Across Remaining Installments":
			return  # spread is handled by totals + redistribute

		amt = flt(self.total_amount, precision)

		if method == "Add to Next Installment":
			row = _find_next_pending_row(booking, precision)
			if not row:
				# No pending row -> add as a new installment at next due date
				booking.append(
					"installment_schedule",
					{
						"installment_name": f"Other Charges: {self.name}",
						"due_date": getdate(self.effective_from or booking.next_due_date or booking.installment_start or booking.booking_date),
						"due_amount": amt,
						"paid_amount": 0,
						"status": "Unpaid",
					},
				)
				return

			# Increase next pending due by full amount (paid part stays)
			row.due_amount = flt(flt(row.due_amount, precision) + amt, precision)
			return

		if method == "Create New Installment":
			due_date = getdate(self.effective_from or booking.next_due_date or booking.installment_start or booking.booking_date)
			booking.append(
				"installment_schedule",
				{
					"installment_name": f"Other Charges: {self.name}",
					"due_date": due_date,
					"due_amount": amt,
					"paid_amount": 0,
					"status": "Unpaid",
				},
			)
			return

	def _try_reverse_special_allocation(self, booking, precision):
		"""
		Reverse only if safe:
		- Add to Next Installment: subtract from the earliest pending row ONLY if its paid_amount == 0
		- Create New Installment: remove last matching row ONLY if unpaid and paid_amount == 0
		If not safe -> block cancel (because audit/payment already affected).
		"""
		method = (self.allocation_method or "").strip()
		amt = flt(self.total_amount, precision)

		if method == "Spread Across Remaining Installments":
			return  # pure spread will be auto handled by totals + redistribute

		if method == "Add to Next Installment":
			row = _find_next_pending_row(booking, precision)
			if not row:
				return

			if flt(row.paid_amount, precision) != 0:
				frappe.throw("Cannot cancel this Other Charges Invoice because the next installment has payments. Ask Manager/Admin.")

			new_due = flt(flt(row.due_amount, precision) - amt, precision)
			if new_due < 0:
				new_due = 0
			row.due_amount = new_due
			return

		if method == "Create New Installment":
			# remove last row if it matches our injected pattern and is unpaid
			rows = list(booking.installment_schedule or [])
			if not rows:
				return
			last = rows[-1]

			if (last.installment_name or "") != f"Other Charges: {self.name}":
				# Not safe to guess -> block cancel
				frappe.throw("Cannot cancel because the created installment row cannot be matched safely. Ask Manager/Admin.")

			if flt(last.paid_amount, precision) != 0:
				frappe.throw("Cannot cancel because the created installment already has payment. Ask Manager/Admin.")

			# remove it
			booking.installment_schedule.pop()
			return
