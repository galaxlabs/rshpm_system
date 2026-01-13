# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate


def _has_column(doctype: str, fieldname: str) -> bool:
	try:
		return bool(frappe.db.has_column(f"tab{doctype}", fieldname))
	except Exception:
		return False


def _get_precision(doctype: str, fieldname: str, default: int = 2) -> int:
	try:
		p = frappe.get_precision(doctype, fieldname)
		return int(p) if p is not None else default
	except Exception:
		return default


def _get_booking(payment_doc: Document):
	"""
	Preferred: Payment.booking (Link to Booking).
	Fallback: find latest Booking matching customer+property (if Payment.booking doesn't exist yet).
	"""
	if getattr(payment_doc, "booking", None):
		return frappe.get_doc("Booking", payment_doc.booking)

	# fallback (only if you haven't added Payment.booking yet)
	if payment_doc.customer and payment_doc.property:
		booking_name = frappe.db.get_value(
			"Booking",
			{"customer": payment_doc.customer, "property": payment_doc.property},
			"name",
			order_by="modified desc",
		)
		if booking_name:
			return frappe.get_doc("Booking", booking_name)

	frappe.throw("Please set Booking in Payment (recommended) or ensure a Booking exists for this Customer + Property.")


def _calc_down_payment_amount(booking: Document, precision: int) -> float:
	total_cost = flt(booking.total_cost, precision)
	dp_pct = flt(booking.down_payment_percentage)
	return flt(total_cost * (dp_pct / 100.0), precision)


def _sum_payments_for_booking(booking_name: str, payment_type: str = None) -> float:
	filters = {"booking": booking_name} if _has_column("Payment", "booking") else {}
	if not filters:
		# if Payment.booking not added yet, we cannot reliably sum by booking
		return 0.0

	if payment_type:
		filters["payment_type"] = payment_type

	# docstatus=1 means submitted
	return flt(frappe.db.get_value("Payment", filters, "SUM(amount_paid)") or 0)


def _update_booking_summary(booking: Document):
	precision = _get_precision("Booking", "total_cost", default=2)

	down_payment_amount = _calc_down_payment_amount(booking, precision)
	total_cost = flt(booking.total_cost, precision)

	# If Payment.booking exists, compute totals from submitted payments.
	# Otherwise, we update using schedule only (limited).
	down_payment_paid = _sum_payments_for_booking(booking.name, "Down Payment")
	installments_paid_total = _sum_payments_for_booking(booking.name, "Installment")
	token_paid = _sum_payments_for_booking(booking.name, "Token")

	total_paid = flt(down_payment_paid + installments_paid_total + token_paid, precision)

	# Remaining balance for plan typically excludes token (your choice).
	# Here: Remaining = total_cost - (down_payment_paid + installments_paid_total)
	remaining_balance = flt(total_cost - (down_payment_paid + installments_paid_total), precision)

	# Compute next due + overdue amount from schedule
	next_due_date = None
	overdue_amount = 0.0
	today = getdate(nowdate())

	if booking.get("installment_schedule"):
		for row in sorted(booking.installment_schedule, key=lambda r: r.due_date or today):
			due_amount = flt(row.due_amount, precision)
			paid_amount = flt(row.paid_amount, precision)
			remaining_due = flt(due_amount - paid_amount, precision)

			# overdue
			if row.due_date and getdate(row.due_date) < today and remaining_due > 0:
				overdue_amount += remaining_due

			# next due (first unpaid / partial)
			if not next_due_date and remaining_due > 0:
				next_due_date = row.due_date

	overdue_amount = flt(overdue_amount, precision)

	# Advance = any extra money beyond what schedule currently requires
	# Simple version: if remaining_balance < 0, it's advance/overpay.
	advance_amount = flt(abs(remaining_balance), precision) if remaining_balance < 0 else 0.0

	# Set fields only if they exist (so code won't crash if you haven't added them yet)
	to_set = {}
	if _has_column("Booking", "down_payment_amount"):
		to_set["down_payment_amount"] = down_payment_amount
	if _has_column("Booking", "down_payment_paid"):
		to_set["down_payment_paid"] = down_payment_paid
	if _has_column("Booking", "installments_paid_total"):
		to_set["installments_paid_total"] = installments_paid_total
	if _has_column("Booking", "total_paid"):
		to_set["total_paid"] = total_paid
	if _has_column("Booking", "remaining_balance"):
		to_set["remaining_balance"] = remaining_balance
	if _has_column("Booking", "next_due_date"):
		to_set["next_due_date"] = next_due_date
	if _has_column("Booking", "overdue_amount"):
		to_set["overdue_amount"] = overdue_amount
	if _has_column("Booking", "advance_amount"):
		to_set["advance_amount"] = advance_amount

	if to_set:
		frappe.db.set_value("Booking", booking.name, to_set, update_modified=False)


def _allocate_installment_payment(booking: Document, amount: float) -> float:
	"""
	Allocate 'amount' into booking.installment_schedule.
	Returns leftover (advance) amount.
	"""
	precision = _get_precision("Booking", "total_cost", default=2)
	remaining = flt(amount, precision)

	if not booking.get("installment_schedule"):
		return remaining

	# Pay earliest due first
	rows = sorted(booking.installment_schedule, key=lambda r: r.due_date or getdate(nowdate()))

	for row in rows:
		if remaining <= 0:
			break

		due = flt(row.due_amount, precision)
		paid = flt(row.paid_amount, precision)
		balance = flt(due - paid, precision)

		if balance <= 0:
			# already paid
			row.status = "Paid"
			continue

		alloc = remaining if remaining <= balance else balance
		row.paid_amount = flt(paid + alloc, precision)
		remaining = flt(remaining - alloc, precision)

		# status update using your allowed values :contentReference[oaicite:6]{index=6}
		if row.paid_amount >= due:
			row.status = "Paid"
		elif row.paid_amount > 0:
			row.status = "Partially Paid"
		else:
			row.status = "Unpaid"

	booking.save(ignore_permissions=True)
	return remaining


def on_submit_payment(doc, method=None):
	"""
	Doc Event: Payment on_submit
	- Allocates installment payments automatically
	- Updates Booking summaries
	"""
	booking = _get_booking(doc)

	amount_paid = flt(doc.amount_paid)
	if amount_paid <= 0:
		frappe.throw("Amount Paid must be greater than 0.")

	p_type = doc.payment_type  # Token / Down Payment / Installment :contentReference[oaicite:7]{index=7}

	# Auto allocate only for Installment payments
	leftover = 0.0
	if p_type == "Installment":
		leftover = _allocate_installment_payment(booking, amount_paid)

		# mark leftover as advance if fields exist
		if _has_column("Payment", "unallocated_amount"):
			frappe.db.set_value("Payment", doc.name, "unallocated_amount", leftover, update_modified=False)
		if _has_column("Payment", "is_advance"):
			frappe.db.set_value("Payment", doc.name, "is_advance", 1 if leftover > 0 else 0, update_modified=False)

	# For Down Payment and Token: no schedule allocation here.
	# (Down payment is tracked in Booking summary; token can be used for lock/QR later.)

	_update_booking_summary(booking)


def on_cancel_payment(doc, method=None):
	"""
	Doc Event: Payment on_cancel
	For correctness, you should reverse allocations.
	Simple approach: recompute paid_amount from scratch based on submitted payments.
	(We can implement that next if you want.)
	"""
	booking = _get_booking(doc)
	_update_booking_summary(booking)


class Payment(Document):
	pass
