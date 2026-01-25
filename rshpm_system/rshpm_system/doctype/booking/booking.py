# Copyright (c) 2026, Galaxy Labs
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import add_months, flt, getdate, now_datetime


def _to_int(val, default=0) -> int:
	try:
		if val is None or val == "":
			return default
		return int(val)
	except Exception:
		return default


def _get_currency_precision(doctype: str, fieldname: str, default: int = 2) -> int:
	try:
		p = frappe.get_precision(doctype, fieldname)
		return int(p) if p is not None else default
	except Exception:
		return default


def _interval_months(label: str) -> int:
	if label == "Monthly":
		return 1
	if label == "Quarterly":
		return 3
	# if Custom or unknown, default 1 (we won't auto-generate for Custom plan anyway)
	return 1


class Booking(Document):

	# ---------------------------
	# VALIDATE (runs on Save)
	# ---------------------------
	def validate(self):
		self._validate_required_links()
		self._validate_property_is_bookable()

		# keep money fields consistent
		self._recalculate_totals()

		# auto-generate schedule only for Fixed plans when empty
		if (self.payment_plan_type or "") == "Fixed":
			self._generate_installment_schedule_if_empty()

		# after schedule exists (fixed/custom), refresh derived fields
		self._refresh_due_metrics()

	def _validate_required_links(self):
		if not self.company:
			frappe.throw("Company is required.")
		if not self.customer:
			frappe.throw("Customer is required.")
		if not self.property:
			frappe.throw("Property is required.")
		if not self.booking_date:
			frappe.throw("Booking Date is required.")

	def _validate_property_is_bookable(self):
		"""
		Prevents double-selling.
		Allowed to book only if Property.status is Inventory OR Reserved (valid reservation).
		Blocks if already booked/allotted/possession/transferred/etc.
		"""
		prop = frappe.db.get_value(
			"Property",
			self.property,
			["status", "current_booking", "reserved_till", "company"],
			as_dict=True,
		)

		if not prop:
			frappe.throw(f"Property '{self.property}' not found.")

		# company consistency
		if prop.company and self.company and prop.company != self.company:
			frappe.throw("Property company and Booking company must be the same.")

		status = (prop.status or "").strip()
		current_booking = (prop.current_booking or "").strip()

		# If another booking already holds it, block
		if current_booking and current_booking != self.name and status in (
			"Booked", "Allotted", "Possession", "Transferred", "Cancelled", "Blocked"
		):
			frappe.throw(f"Property is already {status} under Booking: {current_booking}")

		# Allowed statuses
		if status == "Inventory":
			return

		if status == "Reserved":
			# allow only if reservation not expired
			if prop.reserved_till:
				if now_datetime() > prop.reserved_till:
					frappe.throw("Property reservation has expired. Set it back to Inventory or extend reserved till.")
			# If reserved and already attached to another booking, block
			if current_booking and current_booking != self.name:
				frappe.throw(f"Property is Reserved under another Booking: {current_booking}")
			return

		# Anything else is not bookable
		frappe.throw(f"Property is not bookable because current status is '{status}'.")

	# ---------------------------
	# SUBMIT (locks property)
	# ---------------------------
	def on_submit(self):
		self._lock_property_as_booked()
		self.db_set("status", "Submitted")

		# update status field for your UI (optional)
		if (self.status or "") in ("", "Draft"):
			self.db_set("status", "Submitted")

	def _lock_property_as_booked(self):
		"""
		When Booking is submitted:
		- Property.status = Booked
		- Property.current_booking = this booking
		- Property.is_owner_locked = 1
		"""
		if not self.property:
			return

		# double-check current status at submit time (race condition protection)
		prop = frappe.db.get_value(
			"Property",
			self.property,
			["status", "current_booking"],
			as_dict=True,
		)

		if not prop:
			frappe.throw("Property not found at submit time.")

		status = (prop.status or "").strip()
		current_booking = (prop.current_booking or "").strip()

		# If already booked by some other booking => stop
		if current_booking and current_booking != self.name and status in ("Booked", "Allotted", "Possession", "Transferred"):
			frappe.throw(f"Property already locked under Booking: {current_booking}")

		frappe.db.set_value("Property", self.property, {
			"status": "Booked",
			"current_booking": self.name,
			"is_owner_locked": 1,
			"booked_by": self.customer,
		})

	# ---------------------------
	# CANCEL (release property)
	# ---------------------------
	def on_cancel(self):
		self._block_cancel_if_payments_exist()
		self._release_property_if_owned_by_this_booking()

		# update status field for UI (optional)
		self.db_set("status", "Cancelled")

	def _block_cancel_if_payments_exist(self):
		"""
		If any submitted Payment exists against this Booking, block cancel unless Manager/System Manager.
		"""
		if not self.name:
			return

		has_payment = frappe.db.exists(
			"Payment",
			{"booking": self.name, "docstatus": 1}
		)

		if has_payment and not (frappe.has_role("Manager") or frappe.has_role("System Manager")):
			frappe.throw("Cannot cancel Booking because payments exist. Ask Manager to cancel.")

	def _release_property_if_owned_by_this_booking(self):
		if not self.property:
			return

		prop = frappe.db.get_value(
			"Property",
			self.property,
			["current_booking", "status"],
			as_dict=True,
		)
		if not prop:
			return

		if (prop.current_booking or "") != self.name:
			# don't touch property if it isn't locked by this booking
			return

		# release to Inventory on cancel (you can change to Cancelled if your policy says so)
		frappe.db.set_value("Property", self.property, {
			"status": "Inventory",
			"current_booking": None,
			"is_owner_locked": 0,
			"booked_by": None,
		})

	# ---------------------------
	# MONEY + SCHEDULE
	# ---------------------------
	def _recalculate_totals(self):
		precision = _get_currency_precision("Booking", "total_cost", default=2)

		total_cost = flt(self.total_cost, precision)
		discount = flt(self.discount, precision)
		net_total = flt(total_cost - discount, precision)
		if net_total < 0:
			frappe.throw("Discount cannot be greater than Total Cost.")

		self.net_total = net_total

		# Down payment logic
		dp_amt = flt(self.down_payment_amount, precision)
		dp_pct = flt(self.down_payment_percentage, 2)

		# If amount missing but pct exists => compute amount
		if (dp_amt == 0) and (dp_pct > 0):
			dp_amt = flt(net_total * (dp_pct / 100.0), precision)
			self.down_payment_amount = dp_amt

		# If amount exists but pct missing => compute pct
		if (dp_amt > 0) and (dp_pct == 0) and net_total > 0:
			dp_pct = flt((dp_amt / net_total) * 100.0, 2)
			self.down_payment_percentage = dp_pct

		# If both exist, keep amount as source-of-truth and recalc pct to match net_total
		if (dp_amt > 0) and (net_total > 0):
			self.down_payment_percentage = flt((dp_amt / net_total) * 100.0, 2)

		# Paid totals (these should be updated by Payment logic, but keep safe)
		down_paid = flt(self.down_payment_paid, precision)
		inst_paid = flt(self.installments_paid_total, precision)
		total_paid = flt(down_paid + inst_paid, precision)

		self.total_paid = total_paid
		self.remaining_balance = flt(net_total - total_paid, precision)

		# advance if overpaid
		self.advance_amount = flt(max(0, total_paid - net_total), precision)

	def _generate_installment_schedule_if_empty(self):
		"""
	Generate schedule only if table empty and Fixed plan selected.
	Uses (net_total - down_payment_amount) as installment total.
	"""
		if self.installment_schedule:
			return

		precision = _get_currency_precision("Booking", "total_cost", default=2)

		if not self.total_installments or int(self.total_installments) <= 0:
			frappe.throw("Total Installments must be greater than 0 for Fixed plan.")

		total_installments = int(self.total_installments)

		installment_total = flt(self.net_total - flt(self.down_payment_amount, precision), precision)
		if installment_total < 0:
			frappe.throw("Down Payment Amount cannot be greater than Net Total.")

		# If installment_total is 0, still allow but create 0 rows? Better: block.
		if installment_total == 0:
			frappe.throw("Installment total is 0. Either set down payment smaller or use Custom plan.")

		months = _interval_months(self.installment_interval)

		# equal split + last adjustment for rounding
		base = flt(installment_total / total_installments, precision)
		first_total = flt(base * (total_installments - 1), precision)
		last = flt(installment_total - first_total, precision)

		start_date = getdate(self.booking_date)

		for i in range(total_installments):
			due_date = add_months(start_date, (i + 1) * months)
			amt = last if i == total_installments - 1 else base

			self.append("installment_schedule", {
				"installment_name": f"Installment {i + 1}",
				"due_date": due_date,
				"due_amount": amt,
				"paid_amount": 0,
				"status": "Unpaid",
			})

	def _refresh_due_metrics(self):
		"""
	Compute:
	- next_due_date
	- overdue_amount (after grace days)
	"""
		precision = _get_currency_precision("Booking", "total_cost", default=2)
		grace = _to_int(self.grace_days, default=0)

		today = getdate()
		next_due = None
		overdue = 0.0

		for row in (self.installment_schedule or []):
			due_date = row.due_date
			due_amt = flt(row.due_amount, precision)
			paid_amt = flt(row.paid_amount, precision)
			pending = flt(due_amt - paid_amt, precision)

			if pending <= 0:
				# mark paid if not already
				if row.status != "Paid":
					row.status = "Paid"
				continue

			# set row status
			if paid_amt > 0:
				row.status = "Partially Paid"
			else:
				row.status = "Unpaid"

			# next due = earliest pending
			if due_date and (next_due is None or due_date < next_due):
				next_due = due_date

			# overdue = due_date passed beyond grace
			if due_date:
				days_late = (today - getdate(due_date)).days
				if days_late > grace:
					overdue = flt(overdue + pending, precision)
					row.status = "Overdue"

		self.next_due_date = next_due
		self.overdue_amount = flt(overdue, precision)
