# Entering leave days for staff (d59)

HR & Admin > Leave now has a **Record leave** form and a **Leave taken in <year>** history, so leave can be entered directly, without the staff member filling a timesheet.

## What it does

- **Who:** a Supervisor, Partner or Admin records leave for any active internal staff member; any other user records only their own (their login must be linked to a Payroll employee profile, Payroll > Employees).
- **Form:** staff member, leave type, first day, last day, days, optional note. **Days are worked out automatically as working days (Monday to Friday)** between the dates; type over them for a half day (0.5) or to leave out a public holiday. The form shows the automatic figure as you pick the dates.
- **Annual/Vacation Leave** (the tracked, accumulative type): checked against the available balance and the minimum-service rule (12 months), then taken off the balance at once (`LeaveBalance.taken_days`). Deleting the record puts the days back.
- **Sick, Special/Compassionate, Maternity** (statutory provisions): recorded for the history, no balance kept, as before.
- **Checks:** last day not before first day; at most a year per record; days not more than the calendar days covered; no overlap with another record for the same person; a range with no working days is refused unless days are typed.
- **History:** every record for the year with who entered it and a total, filterable by staff member (reviewers) and year. Leave captured on a **timesheet** shows in the same list marked "on timesheet", so nothing is missed; timesheet leave still works exactly as before and is not duplicated.
- **Delete:** reviewers can delete any record; a staff member can delete only the records they entered for themselves.

## Files

`models.py` (`LeaveRecord`, new table created automatically), `leave_calc.py` (`weekdays_between`, `check_can_take`), `hr.py` (`list_leave_balances` context, `_leave_history`, `record_leave`, `delete_leave_record`), `templates/hr/leave_balances.html`, `smoke_test_leave_records.py` (39 checks).

## Not done / for later

- Approval step for staff-entered leave (it is recorded straight away, like timesheet leave).
- Public holiday calendar (days can be typed over instead).
- Leave on payslips / payroll deductions for unpaid or half-pay sick leave.
- A team calendar of who is away.
