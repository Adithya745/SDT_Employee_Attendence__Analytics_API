# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | | | | |

Also note anything you looked at and decided was **not** a defect, and why.
# REVIEW.md

List of defects found in the starter `app/main.py`:

| # | Where (function) | What is wrong | How you'd notice it | How I fixed it |
|---|------------------|---------------|---------------------|------------------|
| 1 | `list_employees` | `skip = page * page_size` (off-by-one) | Page 1 returns items starting from index 20 instead of 0 | Changed to `skip = (page - 1) * page_size` |
| 2 | `list_employees` | `total` always counts all employees, ignoring `department` filter | Total number is wrong when filtering by department | Changed to `total = db.employees.count_documents(q)` |
| 3 | `list_employees` | No sort order | Employees come in random order | Added `.sort("emp_code", 1)` |
| 4 | `punch_in` | No check if employee exists | Calling with unknown `emp_code` causes `TypeError` (NoneType) | Added `if not emp: raise HTTPException(404, "employee not found")` |
| 5 | `punch_in` | Date is calculated without converting to IST | Attendance date is wrong for punches near midnight | Convert timestamp to IST timezone before taking `.date()` |
| 6 | `punch_in` | Response returns Python datetime objects | Contract requires epoch milliseconds (integers) | Convert all datetime fields to `int(ts.timestamp() * 1000)` before returning |
| 7 | `punch_in` | Returns an `"id"` field | OpenAPI contract says attendance records have no `id` | Removed the `id` field completely |
| 8 | `compute_late_minutes` | Does not truncate to whole seconds (R1) | Sub-second differences can change late calculation | Truncate seconds and microseconds before calculation |
| 9 | `compute_late_minutes` | Late logic is fragile and does not match R2 exactly | 09:40:00 vs 09:40:01 boundary fails | Rewrote to match R2 strictly (grace = 10 minutes, floor minutes since shift_start) |
| 10 | `compute_work_hours` | Uses Python `round()` (banker's rounding) | Values like 4.495 can round incorrectly | Replaced with `Decimal` + `ROUND_HALF_UP` |
| 11 | `compute_overtime` | Does not handle overnight shifts | Overnight shifts (22:00–06:00) give wrong overtime | Fixed to handle overnight shift end correctly (R1 + R3) |
| 12 | `health` | Always returns 200 | Contract requires 503 when MongoDB is unreachable | Added MongoDB ping; return 503 on failure |
| 13 | Startup | No indexes are created | Slow queries + race conditions on concurrent punch-in | Created unique index on `(emp_code, date)` and other useful indexes at startup |
| 14 | `list_attendance` | Loads all matching documents into memory then paginates | Very slow and memory-heavy on large data | Changed to use MongoDB `.skip()` + `.limit()` + `.sort()` |
| 15 | `list_attendance` | Returns `"id"` field | Same contract violation as punch-in | Removed `"id"` field |
| 16 | Whole file | Many endpoints missing | Only 4 of 12 required endpoints exist | Implemented the remaining endpoints |

### Things I checked and decided were **not** defects

- Using `pymongo` (synchronous) instead of Motor is acceptable because the problem statement allows both.
- Loading `.env` with `python-dotenv` is fine for local development (real environment variables still win).
- Default shift times (`09:30` / `18:30`) match the sample data.