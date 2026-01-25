# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

import re
import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate

# -----------------------------
# Helpers
# -----------------------------

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


def _digits(value: str) -> str:
	return re.sub(r"\D+", "", value or "")


def _ensure_company(doc: Document):
	"""Set company from booking or defaults."""
	if getattr(doc, "company", None):
		return

	# Prefer booking.company
	if getattr(doc, "booking", None):
		c = frappe.db.get_value("Booking", doc.booking, "company")
		if c:
			doc.company = c
			return

	# User default
	user_default_company = frappe.defaults.get_user_default("Company")
	if user_default_company:
		doc.company = user_default_company
		return

	# Fallback
	doc.company = frappe.db.get_value("Company", {}, "name")


def _get_booking(payment_doc: Document) -> Document:
	"""
	Required: Payment.booking (Link to Booking).
	Fallback is dangerous; so we enforce booking as mandatory in validate().
	"""
	if getattr(payment_doc, "booking", None):
		return frappe.get_doc("Booking", payment_doc.booking)

	frappe.throw("Booking is required in Payment.")


def _calc_down_payment_amount(booking: Document, precision: int) -> float:
	# Booking already computes dp amount sometimes, but keep consistent
	net_total = flt(getattr(booking, "net_total", 0), precision)
	dp_amt = flt(getattr(booking, "down_payment_amount", 0), precision)
	dp_pct = flt(getattr(booking, "down_payment_percentage", 0), 2)

	if dp_amt > 0:
		return dp_amt
	if dp_pct > 0 and net_total > 0:
		return flt(net_total * (dp_pct / 100.0), precision)
	return 0.0


def _sum_submitted_payments(booking_name: str, payment_type: str = None) -> float:
	filters = {"booking": booking_name, "docstatus": 1}
	if payment_type:
		filters["payment_type"] = payment_type

	return flt(frappe.db.get_value("Payment", filters, "SUM(amount_paid)") or 0)


def _recompute_schedule_paid_amount_from_payments(booking: Document):
	"""
	Rebuild Booking.installment_schedule.paid_amount from Payment Allocation ledger.
	This makes cancel & edits safe.
	"""
	precision = _get_precision("Booking", "total_cost", default=2)

	# Reset schedule
	for row in (booking.get("installment_schedule") or []):
		row.paid_amount = 0
		row.status = "Unpaid"

	# Map schedule row.id -> schedule row
	schedule_map = {row.name: row for row in (booking.get("installment_schedule") or [])}

	# Get all submitted installment payments (order by creation)
	payment_names = frappe.db.get_all(
		"Payment",
		filters={"booking": booking.name, "docstatus": 1, "payment_type": "Installment"},
		pluck="name",
		order_by="creation asc",
	)

	for p_name in payment_names:
		p = frappe.get_doc("Payment", p_name)
		for alloc in (p.get("payment_allocation") or []):
			key = getattr(alloc, "schedule_row_name", None)
			if not key or key not in schedule_map:
				continue

			srow = schedule_map[key]
			srow.paid_amount = flt(srow.paid_amount + flt(alloc.allocated_amount, precision), precision)

	# Refresh status flags
	for row in (booking.get("installment_schedule") or []):
		due = flt(row.due_amount, precision)
		paid = flt(row.paid_amount, precision)
		if due > 0 and paid >= due:
			row.status = "Paid"
		elif paid > 0:
			row.status = "Partially Paid"
		else:
			row.status = "Unpaid"

	booking.save(ignore_permissions=True)


def _refresh_booking_due_metrics(booking: Document):
	"""
	Use your existing Booking style:
	- next_due_date
	- overdue_amount (without penalty logic here; you do grace-based overdue in Booking.validate)
	"""
	precision = _get_precision("Booking", "total_cost", default=2)
	today = getdate(nowdate())

	next_due_date = None
	overdue_amount = 0.0

	if booking.get("installment_schedule"):
		for row in sorted(booking.installment_schedule, key=lambda r: r.due_date or today):
			due = flt(row.due_amount, precision)
			paid = flt(row.paid_amount, precision)
			pending = flt(due - paid, precision)

			if pending <= 0:
				continue

			if not next_due_date:
				next_due_date = row.due_date

			if row.due_date and getdate(row.due_date) < today:
				overdue_amount = flt(overdue_amount + pending, precision)

	booking.next_due_date = next_due_date
	booking.overdue_amount = flt(overdue_amount, precision)


def _update_booking_summary(booking: Document):
	precision = _get_precision("Booking", "total_cost", default=2)

	# Paid totals from submitted payments
	down_payment_paid = _sum_submitted_payments(booking.name, "Down Payment")
	installments_paid_total = _sum_submitted_payments(booking.name, "Installment")
	token_paid = _sum_submitted_payments(booking.name, "Token")

	total_paid = flt(down_payment_paid + installments_paid_total + token_paid, precision)

	# booking totals
	total_cost = flt(getattr(booking, "total_cost", 0), precision)
	discount = flt(getattr(booking, "discount", 0), precision)
	net_total = flt(getattr(booking, "net_total", (total_cost - discount)), precision)

	down_payment_amount = _calc_down_payment_amount(booking, precision)

	# Remaining balance: net_total minus all submitted payments (including token)
	remaining_balance = flt(net_total - total_paid, precision)

	# Advance
	advance_amount = flt(abs(remaining_balance), precision) if remaining_balance < 0 else 0.0

	# next due / overdue from schedule
	_refresh_booking_due_metrics(booking)

	# Set values safely (only if fields exist)
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
	if _has_column("Booking", "advance_amount"):
		to_set["advance_amount"] = advance_amount
	if _has_column("Booking", "next_due_date"):
		to_set["next_due_date"] = booking.next_due_date
	if _has_column("Booking", "overdue_amount"):
		to_set["overdue_amount"] = booking.overdue_amount

	if to_set:
		frappe.db.set_value("Booking", booking.name, to_set, update_modified=False)


def _allocate_installment_payment_and_write_ledger(payment: Document, booking: Document, amount: float) -> float:
	"""
	Allocate installment payment to booking.installment_schedule (earliest due first).
	Write allocation rows into payment.payment_allocation.
	Return leftover (advance) amount.
	"""
	precision = _get_precision("Booking", "total_cost", default=2)
	remaining = flt(amount, precision)

	# Clear any existing ledger rows (important if draft saved then edited)
	payment.set("payment_allocation", [])

	if not booking.get("installment_schedule"):
		return remaining

	today = getdate(nowdate())
	rows = sorted(booking.installment_schedule, key=lambda r: r.due_date or today)

	for row in rows:
		if remaining <= 0:
			break

		due = flt(row.due_amount, precision)
		paid = flt(row.paid_amount, precision)
		balance = flt(due - paid, precision)

		if balance <= 0:
			continue

		alloc = remaining if remaining <= balance else balance

		# Update booking schedule row
		row.paid_amount = flt(paid + alloc, precision)

		if row.paid_amount >= due and due > 0:
			row.status = "Paid"
		elif row.paid_amount > 0:
			row.status = "Partially Paid"
		else:
			row.status = "Unpaid"

		# Write ledger row (matches your Payment Allocation fields)
		payment.append("payment_allocation", {
			"schedule_row_name": row.name,  # REQUIRED FIELD (add to Payment Allocation)
			"installment_name": row.installment_name,
			"due_date": row.due_date,       # REQUIRED FIELD (add to Payment Allocation)
			"allocated_amount": alloc,
			"allocated_date": getdate(payment.payment_date) if getattr(payment, "payment_date", None) else getdate(nowdate()),
			# note/attachments are user-controlled; we don't touch them here
		})

		remaining = flt(remaining - alloc, precision)

	# Save booking once (not per row)
	booking.save(ignore_permissions=True)

	return remaining


# -----------------------------
# DocType
# -----------------------------

class Payment(Document):
	def validate(self):
		_ensure_company(self)

		if not self.booking:
			frappe.throw("Booking is required.")

		if not self.amount_paid or flt(self.amount_paid) <= 0:
			frappe.throw("Amount Paid must be greater than 0.")

		if not self.payment_date:
			frappe.throw("Payment Date is required.")

		# Normalize reference numbers (optional)
		if getattr(self, "receiptinvoice_number", None):
			self.receiptinvoice_number = str(self.receiptinvoice_number).strip()

		# Consistency checks with booking
		booking = _get_booking(self)

		# enforce company match
		if booking.company and self.company and booking.company != self.company:
			frappe.throw("Payment company and Booking company must be the same.", title="Company Mismatch")

		# optional: enforce customer/property match
		if getattr(self, "customer", None) and booking.customer and self.customer != booking.customer:
			frappe.throw("Payment customer must match booking customer.", title="Mismatch")
		if getattr(self, "property", None) and booking.property and self.property != booking.property:
			frappe.throw("Payment property must match booking property.", title="Mismatch")

	def on_submit(self):
		booking = _get_booking(self)
		precision = _get_precision("Booking", "total_cost", default=2)

		amount_paid = flt(self.amount_paid, precision)

		# Allocate only for installment payments
		leftover = 0.0
		if (self.payment_type or "") == "Installment":
			leftover = _allocate_installment_payment_and_write_ledger(self, booking, amount_paid)

			# set flags on Payment
			if _has_column("Payment", "unallocated_amount"):
				self.unallocated_amount = flt(leftover, precision)
			if _has_column("Payment", "is_advance"):
				self.is_advance = 1 if leftover > 0 else 0

			# Ensure ledger rows + flags are persisted
			self.db_update()

		# Update booking totals
		_update_booking_summary(booking)

	def on_cancel(self):
		booking = _get_booking(self)

		# Recompute schedule from remaining submitted installment payments
		_recompute_schedule_paid_amount_from_payments(booking)

		# Update booking totals
		_update_booking_summary(booking)
