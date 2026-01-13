# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import add_months, flt


def _get_currency_precision(doctype: str, fieldname: str, default: int = 2) -> int:
	"""Get field precision for currency rounding. Falls back to default if missing."""
	try:
		precision = frappe.get_precision(doctype, fieldname)
		return int(precision) if precision is not None else default
	except Exception:
		return default


def _get_interval_months(installment_interval: str) -> int:
	"""Map interval labels to month counts."""
	# Your Booking JSON defines only Monthly and Quarterly
	if installment_interval == "Monthly":
		return 1
	if installment_interval == "Quarterly":
		return 3

	# Safe default (so it doesn't silently become quarterly for unknown values)
	return 1


def _ensure_property_available(property_name: str):
	if not property_name:
		frappe.throw("Property is required.")

	status = frappe.db.get_value("Property", property_name, "status")
	if status is None:
		# Either property not found or field missing
		frappe.throw(f"Property '{property_name}' not found or has no status.")

	if status != "Available":
		frappe.throw(f"Property {property_name} is already {status}.")


def _generate_installment_schedule(doc):
	"""
	Generate installments if schedule table is empty.
	Adjust last installment for rounding so total equals remaining balance.
	"""
	# Prevent duplication
	if doc.installment_schedule:
		return

	# Basic validations
	if not doc.booking_date:
		frappe.throw("Booking Date is required to generate installment schedule.")

	total_cost = flt(doc.total_cost)
	if total_cost <= 0:
		frappe.throw("Total Cost must be greater than 0.")

	down_payment_percentage = flt(doc.down_payment_percentage)
	if down_payment_percentage < 0 or down_payment_percentage > 100:
		frappe.throw("Down Payment Percentage must be between 0 and 100.")

	total_installments = int(doc.total_installments or 0)
	if total_installments <= 0:
		frappe.throw("Total Installments must be greater than 0.")

	# Calculate remaining balance
	down_payment = total_cost * (down_payment_percentage / 100.0)
	remaining_balance = total_cost - down_payment

	if remaining_balance < 0:
		frappe.throw("Remaining balance cannot be negative. Check Total Cost and Down Payment Percentage.")

	interval_months = _get_interval_months(doc.installment_interval)

	precision = _get_currency_precision("Booking", "total_cost", default=2)

	# Split into equal installments (rounded), then fix last row for rounding difference
	base_amount = flt(remaining_balance / total_installments, precision)
	total_assigned_first = flt(base_amount * (total_installments - 1), precision)
	last_amount = flt(remaining_balance - total_assigned_first, precision)

	current_date = doc.booking_date

	for i in range(total_installments):
		due_date = add_months(current_date, (i + 1) * interval_months)

		if i == total_installments - 1:
			due_amount = last_amount
		else:
			due_amount = base_amount

		doc.append("installment_schedule", {
			"installment_name": f"Installment {i + 1}",
			"due_date": due_date,
			"due_amount": due_amount,
			"status": "Unpaid",
		})


def validate_booking(doc, method=None):
	"""
	doc_events validate hook:
	- Ensure property available
	- Generate installments if needed
	"""
	# 1) Check Property Availability
	_ensure_property_available(doc.property)

	# 2) Generate Installment Schedule
	_generate_installment_schedule(doc)


def on_submit_booking(doc, method=None):
	"""
	Optional: On submit, mark property as Booked.
	(You should add 'Booked' option in Property.status for this to be clean.)
	"""
	if doc.property:
		frappe.db.set_value("Property", doc.property, "status", "Booked")


def on_cancel_booking(doc, method=None):
	"""
	Optional: On cancel, revert property back to Available.
	"""
	if doc.property:
		frappe.db.set_value("Property", doc.property, "status", "Available")


class Booking(Document):
	# Keep class clean; we are using hooks.py doc_events
	pass
