# Employee Attendance & Analytics API

FastAPI + MongoDB backend for the HROne Software Engineer Trainee assignment.

## Features

- Employee management
- Punch-in / Punch-out with business rules (R1–R5)
- Manual regularization with full audit trail
- Analytics powered by MongoDB aggregation pipelines
- Explain endpoint for query plans
- Proper indexes and concurrency handling

## Requirements

- Python 3.11+
- MongoDB 6.0+ (local or Atlas)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt