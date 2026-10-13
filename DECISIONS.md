# DECISIONS.md

1. **Indexes.** I created unique indexes on `employees.emp_code` and `attendance_logs.(emp_code, date)`, plus supporting indexes on `(emp_code, punch_in)`, `(date, emp_code)`, `(status, date)`, and `(department, emp_code)`. These protect the identity rules, prevent duplicate punch-ins for the same record day, and speed the filters used by attendance list, analytics, and department queries. I did not keep a broad compound index on every analytics field because the dataset is moderate and over-indexing would slow writes without adding much value.

2. **Punch-in race.** When two identical punch-in requests hit at the same time, each request loads the employee and computes the same record date. The first insert succeeds and writes the attendance document. The second request then sees the duplicate key on `(emp_code, date)` and returns 409, which matches the requirement that exactly one punch-in wins and the rest fail cleanly.

3. **Ties.** The leaderboard ranks by total late minutes, then by employee code as the deterministic tiebreaker. If two employees share the same total at the cutoff, they receive the same rank, and the query keeps only rows whose rank is within the requested limit; this avoids breaking the group-tie rule while still respecting `limit`.

4. **Headcount.** For each department summary, I count employees with `department = X` and `joined_on <= period_end`, regardless of whether a log exists. That ensures zero-log employees are included in headcount and the denominator for daily attendance rate remains correct.

5. **One thing I would change** if this had to serve 100x the data. I would move the heavier monthly and trend aggregations into MongoDB pipelines with pre-aggregated summary collections or materialized monthly snapshots, and keep the API endpoints thin, because the biggest cost is repeated daily/department rollups across large collections.

6. **Trend gap filling and moving average.** The department trend query builds its calendar rows inside a MongoDB aggregation from the department's employee documents, then looks up attendance records per generated day and applies a seven-document window. The requested date range is capped at 92 days, and no Python loop synthesizes output dates.

7. **Overnight overtime.** Overtime is measured against the shift end derived from the punch-in's attendance date, not from the punch-out's calendar date. This keeps an overnight shift's end anchored to the morning after its start.

8. **Instant precision and audit storage.** Request instants are reduced to whole seconds before storage and calculations. Manual history entries retain BSON datetimes for punch-time changes in MongoDB; serialization converts those nested values to epoch milliseconds for the API.

9. **IST weekday aggregation.** Calendar dates are stored as strings, so analytics parse them in `Asia/Kolkata` and evaluate weekdays in that same timezone. This avoids shifting date-only values to the previous UTC day and misclassifying Monday records as Sunday.
