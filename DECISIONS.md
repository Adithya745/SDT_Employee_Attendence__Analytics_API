# DECISIONS.md

1. **Indexes.**  
I created a unique compound index on `(emp_code, date)` on `attendance_logs`. This guarantees that only one attendance record can exist per employee per day and safely handles concurrent punch-in requests (one succeeds, others get 409). I also added indexes on `date`, `late_minutes`, `employees.emp_code`, `department`, and `joined_on` to support list, analytics, and filtering queries efficiently. I considered a single-field index on `status` but rejected it because status alone is not selective enough.

2. **Punch-in race.**  
When two identical punch-in requests arrive at the same instant, both try to insert a document with the same `(emp_code, date)`. Because of the unique index, MongoDB allows only one insert to succeed. The other raises `DuplicateKeyError`, which we catch and convert into a clean HTTP 409 response. No extra locking is needed.

3. **Ties.**  
The late leaderboard sorts by `total_late_minutes` descending, then by `emp_code` ascending. When two employees are tied at the cutoff of `limit`, both are included if their rank is ≤ limit (using `$rank`). The secondary sort by `emp_code` makes the result deterministic.

4. **Headcount.**  
Department summary starts from the `employees` collection and filters by `joined_on <= last day of the month` (R9). It then looks up attendance logs. Employees with zero logs still appear in the headcount because they come from the employees collection, not from the logs.

5. **One thing I would change for 100x data.**  
I would move the heavier analytics (especially department summary and trend) to pre-aggregated daily summary collections updated by a background job, so that queries only read small summary documents instead of scanning the full attendance_logs collection.
