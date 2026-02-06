# Copyright (c) 2026, Galaxy Labs
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import add_months, flt, getdate, now_datetime
from frappe.model.naming import make_autoname


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
	if label == "Semi-Annual":
		return 6
	return 1


class Booking(Document):
	def on_submit(self):
		self._lock_property_as_booked()
		self.status = "Submitted"
		self._auto_set_status()  # in case you want to set Active immediately on submit
		self.db_set("status", self.status)
		  # save status change without triggering another validate/submit cycle
	def on_cancel(self):
		self._release_property_if_owned_by_this_booking()
		self.status = "Cancelled"
		self._auto_set_status()
		self.db_set("status", self.status)

	def validate(self):
		self._validate_required_links()
		self._validate_property_is_bookable()

		# 1) pull other charges total (submitted invoices)
		self._pull_other_charges_total()

		# 2) recalc totals (net_total includes other charges)
		self._recalculate_totals()

		# 3) validate payment mode + plan fields
		self._validate_payment_mode_rules()

		# 4) schedule rules
		if (self.payment_mode or "") == "Installments":
			# Fixed/Flexible: create schedule if empty
			if not self.installment_schedule:
				self._generate_installment_schedule()
			else:
				# if something paid already => do NOT rebuild, only redistribute unpaid when totals changed
				self._redistribute_unpaid_rows_if_needed()

		# 5) derived metrics (next due/overdue/status)
		self._refresh_due_metrics()

		# auto status
		self._auto_set_status()

	def _lock_property_as_booked(self):
		"""
		When Booking is submitted:
		- Property.status = Booked
		- Property.current_booking = this booking
		- Property.is_owner_locked = 1
		- Property.booked_by = customer
		"""
		if not self.property:
			return

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
		if current_booking and current_booking != self.name and status in (
			"Booked", "Allotted", "Possession", "Transferred"
		):
			frappe.throw(f"Property already locked under Booking: {current_booking}")

		frappe.db.set_value(
			"Property",
			self.property,
			{
				"status": "Booked",
				"current_booking": self.name,
				"is_owner_locked": 1,
				"reserved_till": None,
				"booked_by": self.customer,
			},
			update_modified=False,
		)

	def _release_property_if_owned_by_this_booking(self):
		"""
		Release only if:
		- Property.current_booking == this booking
		- Property.status is Reserved or Booked
		"""
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
			return

		if (prop.status or "") not in ("Reserved", "Booked"):
			return

		frappe.db.set_value(
			"Property",
			self.property,
			{
				"status": "Inventory",
				"current_booking": None,
				"is_owner_locked": 0,
				"reserved_till": None,
				"booked_by": "",
			},
			update_modified=False,
		)


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
		Allowed:
		- Property.status == Inventory
		- Property.status == Reserved AND reserved_till not expired AND (current_booking empty or this booking)

		Block:
		- Booked/Allotted/Possession/Transferred/Cancelled/Blocked etc. if current_booking belongs to another booking
		"""

		if not self.property:
			return

		prop = frappe.db.get_value(
			"Property",
			self.property,
			["status", "current_booking", "reserved_till", "company"],
			as_dict=True,
		)

		if not prop:
			frappe.throw(f"Property '{self.property}' not found.")

		# company consistency (your Booking.company is Property Company now)
		# only enforce if Property.company also points to Property Company (same link type)
		if prop.company and self.company and prop.company != self.company:
			frappe.throw("Property company and Booking company must be the same.")

		status = (prop.status or "").strip()
		current_booking = (prop.current_booking or "").strip()

		# If another booking already holds it and it is in a locked status, block
		if current_booking and current_booking != (self.name or "") and status in (
			"Booked", "Allotted", "Possession", "Transferred", "Cancelled", "Blocked"
		):
			frappe.throw(f"Property is already {status} under Booking: {current_booking}")

		if status == "Inventory":
			return

		if status == "Reserved":
			# allow only if reservation not expired
			if prop.reserved_till and now_datetime() > prop.reserved_till:
				frappe.throw("Property reservation has expired. Set it back to Inventory or extend reserved till.")

			# If reserved under another booking, block
			if current_booking and current_booking != (self.name or ""):
				frappe.throw(f"Property is Reserved under another Booking: {current_booking}")
			return

		frappe.throw(f"Property is not bookable because current status is '{status}'.")



	# ---------------------------
	# OTHER CHARGES (from invoices)
	# ---------------------------
	def _pull_other_charges_total(self):
		"""Sum of submitted Other Charges Invoice totals for this booking."""
		if not self.name:
			# Draft new doc may not have name yet; keep 0 for now
			self.other_charges_total = flt(self.other_charges_total) or 0
			return

		total = frappe.db.sql(
			"""
			select ifnull(sum(total_amount), 0)
			from `tabOther Charges Invoice`
			where booking=%s and docstatus=1
			""",
			(self.name,),
		)[0][0]

		self.other_charges_total = flt(total)

	# ---------------------------
	# VALIDATIONS
	# ---------------------------
	def _validate_payment_mode_rules(self):
		mode = (self.payment_mode or "").strip()

		if mode == "Full Payment":
			# keep schedule empty (do not throw if user left it)
			self.total_installments = 0
			self.installment_interval = ""
			self.payment_plan_type = ""
			self.installment_start = None
			# optional: clear schedule always
			# self.set("installment_schedule", [])
			return

		if mode == "Installments":
			if not self.payment_plan_type:
				frappe.throw("Payment Plan Type is required for Installments.")
			if not self.total_installments or int(self.total_installments) <= 0:
				frappe.throw("Total Installments must be greater than 0 for Installments.")
			if not self.installment_interval:
				frappe.throw("Installment Interval is required for Installments.")

	# ---------------------------
	# MONEY + SCHEDULE
	# ---------------------------
	def _recalculate_totals(self):
		precision = _get_currency_precision("Booking", "net_total", default=2)

		total_cost = flt(self.total_cost, precision)
		discount = flt(self.discount, precision)
		other_charges = flt(self.other_charges_total, precision)

		base_net = flt(total_cost - discount, precision)
		if base_net < 0:
			frappe.throw("Discount cannot be greater than Total Cost.")

		# ✅ net_total includes other charges
		net_total = flt(base_net + other_charges, precision)
		self.net_total = net_total

		# Down payment
		dp_amt = flt(self.down_payment_amount, precision)
		dp_pct = flt(self.down_payment_percentage, 2)

		if (dp_amt == 0) and (dp_pct > 0):
			dp_amt = flt(net_total * (dp_pct / 100.0), precision)
			self.down_payment_amount = dp_amt

		if (dp_amt > 0) and (dp_pct == 0) and net_total > 0:
			dp_pct = flt((dp_amt / net_total) * 100.0, 2)
			self.down_payment_percentage = dp_pct

		if (dp_amt > 0) and (net_total > 0):
			self.down_payment_percentage = flt((dp_amt / net_total) * 100.0, 2)

		# Paid totals (safe)
		down_paid = flt(self.down_payment_paid, precision)
		inst_paid = flt(self.installments_paid_total, precision)

		total_paid = flt(down_paid + inst_paid, precision)
		self.total_paid = total_paid

		self.remaining_balance = flt(net_total - total_paid, precision)
		self.advance_amount = flt(max(0, total_paid - net_total), precision)

	def _schedule_base_date(self):
		"""Your rule: schedule starts from installment_start date if provided."""
		return getdate(self.installment_start or self.booking_date)

	def _generate_installment_schedule(self):
		"""
		Create schedule for Installments mode.
	Uses (net_total - down_payment_amount) as installment total.
		"""
		precision = _get_currency_precision("Booking", "net_total", default=2)

		total_installments = int(self.total_installments or 0)
		if total_installments <= 0:
			frappe.throw("Total Installments must be greater than 0.")

		installment_total = flt(self.net_total - flt(self.down_payment_amount, precision), precision)
		if installment_total <= 0:
			frappe.throw("Installment total is 0. Reduce down payment or add installments.")

		months = _interval_months(self.installment_interval)
		start_date = self._schedule_base_date()

		# equal split + rounding last
		base = flt(installment_total / total_installments, precision)
		first_total = flt(base * (total_installments - 1), precision)
		last = flt(installment_total - first_total, precision)

		self.set("installment_schedule", [])

		for i in range(total_installments):
			# if start_date is the "start", first due should be start + interval
			due_date = add_months(start_date, (i + 1) * months)

			amt = last if i == total_installments - 1 else base
			self.append(
				"installment_schedule",
				{
					"installment_name": f"Installment {i + 1}",
					"due_date": due_date,
					"due_amount": amt,
					"paid_amount": 0,
					"status": "Unpaid",
				},
			)

	def _redistribute_unpaid_rows_if_needed(self):
		"""
		If any paid exists, protect paid rows.
		Redistribute only remaining unpaid/pending rows when totals changed (discount/charges/etc).
		"""
		precision = _get_currency_precision("Booking", "net_total", default=2)

		# total installment pool should equal (net_total - down_payment_amount)
		installment_pool = flt(self.net_total - flt(self.down_payment_amount, precision), precision)
		if installment_pool < 0:
			installment_pool = 0

		rows = list(self.installment_schedule or [])
		if not rows:
			return

		paid_any = any(flt(r.paid_amount, precision) > 0 for r in rows)
		if not paid_any:
			# if nothing paid, it’s safe to rebuild exactly to match latest config
			# (but only if you want; keeping as-is is also OK)
			if (self.payment_plan_type or "") in ("Fixed", "Flexible"):
				# if row count mismatched, rebuild
				if len(rows) != int(self.total_installments or 0):
					self._generate_installment_schedule()
			return

		# Paid rows stay as-is; compute how much remains to allocate
		paid_total = 0.0
		pending_rows = []

		for r in rows:
			due = flt(r.due_amount, precision)
			paid = flt(r.paid_amount, precision)
			paid_total = flt(paid_total + min(paid, due), precision)

			pending = flt(due - paid, precision)
			if pending > 0:
				pending_rows.append(r)

		remaining_to_allocate = flt(installment_pool - paid_total, precision)
		if remaining_to_allocate < 0:
			# means overpaid installments; keep future dues minimal (0)
			remaining_to_allocate = 0

		if not pending_rows:
			return

		# redistribute equally across pending rows (rounding last)
		count = len(pending_rows)
		base = flt(remaining_to_allocate / count, precision)
		first_total = flt(base * (count - 1), precision)
		last = flt(remaining_to_allocate - first_total, precision)

		for i, r in enumerate(pending_rows):
			paid = flt(r.paid_amount, precision)
			new_due = (last if i == count - 1 else base) + paid
			r.due_amount = flt(new_due, precision)

	def _refresh_due_metrics(self):
		precision = _get_currency_precision("Booking", "net_total", default=2)
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
				row.status = "Paid"
				continue

			row.status = "Partially Paid" if paid_amt > 0 else "Unpaid"

			if due_date and (next_due is None or due_date < next_due):
				next_due = due_date

			if due_date:
				days_late = (today - getdate(due_date)).days
				if days_late > grace:
					overdue = flt(overdue + pending, precision)
					row.status = "Overdue"

		self.next_due_date = next_due
		self.overdue_amount = flt(overdue, precision)

	def _auto_set_status(self):
		# You want: Active on submit; Completed when remaining_balance <= 0
		if self.docstatus == 1:
			if flt(self.remaining_balance) <= 0:
				self.status = "Completed"
			else:
				self.status = "Active"
		elif self.docstatus == 2:
			self.status = "Cancelled"
		else:
			self.status = "Draft"

	def autoname(self):
		if not self.company:
			frappe.throw("Company is required to generate Booking ID.")

		company_name = frappe.db.get_value("Property Company", self.company, "company_name")
		company_code = (company_name or "").strip().upper().replace(" ", "")[:8]

		if not company_code:
			frappe.throw("Company Code is missing in Property Company.")

		dt = getdate(self.booking_date) if self.booking_date else getdate()
		yyyy = dt.strftime("%Y")
		mm = dt.strftime("%m")

		# PB-<COMP>-<YYYY>-<MM>-00001
		series = f"PB-{company_code}-{yyyy}-{mm}-.#####"
		self.name = frappe.model.naming.make_autoname(series)
