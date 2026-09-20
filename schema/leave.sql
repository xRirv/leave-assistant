PRAGMA foreign_keys = ON;

-- =========================================================
-- STUDENTS
-- =========================================================
CREATE TABLE IF NOT EXISTS student (
    student_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    department TEXT NOT NULL,
    year INTEGER NOT NULL CHECK (year BETWEEN 1 AND 4)
);


-- =========================================================
-- LEAVE BALANCE
-- =========================================================
CREATE TABLE IF NOT EXISTS leave_balance (
    balance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    leave_type TEXT NOT NULL,
    total_leaves INTEGER NOT NULL CHECK (total_leaves >= 0),
    used_leaves INTEGER NOT NULL DEFAULT 0
        CHECK (used_leaves >= 0 AND used_leaves <= total_leaves),

    FOREIGN KEY (student_id)
        REFERENCES student(student_id)
        ON DELETE CASCADE,

    UNIQUE(student_id, leave_type)
);


-- =========================================================
-- HOLIDAY CALENDAR
-- =========================================================
CREATE TABLE IF NOT EXISTS holiday (
    holiday_id INTEGER PRIMARY KEY AUTOINCREMENT,
    holiday_date TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);


-- =========================================================
-- LEAVE REQUESTS
-- =========================================================
CREATE TABLE IF NOT EXISTS leave_request (
    leave_id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    leave_type TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    days INTEGER NOT NULL CHECK (days > 0),
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'WITHDRAWN')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (student_id)
        REFERENCES student(student_id)
        ON DELETE CASCADE,

    CHECK (end_date >= start_date)
);


-- =========================================================
-- NOTIFICATIONS
-- =========================================================
CREATE TABLE IF NOT EXISTS notification (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    leave_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    recipient TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'SENT', 'FAILED')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (leave_id)
        REFERENCES leave_request(leave_id)
        ON DELETE CASCADE,

    FOREIGN KEY (student_id)
        REFERENCES student(student_id)
        ON DELETE CASCADE,
    UNIQUE(leave_id, recipient)
);


-- =========================================================
-- LEAVE POLICY
-- Business rules are stored in the database.
-- =========================================================
CREATE TABLE IF NOT EXISTS policy (
    policy_id INTEGER PRIMARY KEY AUTOINCREMENT,
    leave_type TEXT NOT NULL UNIQUE,
    max_days INTEGER NOT NULL CHECK (max_days > 0),
    requires_hod_approval INTEGER NOT NULL DEFAULT 1
        CHECK (requires_hod_approval IN (0, 1))
);


-- =========================================================
-- SEED STUDENTS
-- =========================================================
INSERT OR IGNORE INTO student
    (student_id, name, email, department, year)
VALUES
    (1, 'Arun Kumar', 'arun@college.edu', 'CSE', 2),
    (2, 'Priya Sharma', 'priya@college.edu', 'CSE', 3),
    (3, 'Rahul Raj', 'rahul@college.edu', 'ECE', 2);


-- =========================================================
-- SEED LEAVE BALANCES
-- =========================================================
INSERT OR IGNORE INTO leave_balance
    (student_id, leave_type, total_leaves, used_leaves)
VALUES
    (1, 'CASUAL', 12, 2),
    (1, 'SICK', 10, 1),
    (2, 'CASUAL', 12, 4),
    (2, 'SICK', 10, 2),
    (3, 'CASUAL', 12, 3),
    (3, 'SICK', 10, 0);


-- =========================================================
-- SEED HOLIDAYS
-- =========================================================
INSERT OR IGNORE INTO holiday
    (holiday_date, name)
VALUES
    ('2026-10-02', 'Gandhi Jayanti'),
    ('2026-10-20', 'Diwali'),
    ('2026-12-25', 'Christmas');


-- =========================================================
-- SEED LEAVE POLICIES
-- =========================================================
INSERT OR IGNORE INTO policy
    (leave_type, max_days, requires_hod_approval)
VALUES
    ('CASUAL', 5, 1),
    ('SICK', 7, 1);