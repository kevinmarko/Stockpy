Analyze the provided source code for Step 1. Verify the following:
1. VECTORIZATION: Ensure all DataFrame operations utilize vectorized Pandas/NumPy functions. Fail the code if iterrows(), itertuples(), or standard for loops are used for data mutation.
2. DATABASE ARCHITECTURE: Verify the presence of a relational database implementation (SQLite/PostgreSQL) using SQLAlchemy or direct adapters (like psycopg2).
3. SCHEMA RIGIDITY: Check for explicitly defined schemas, primary keys, and foreign key relationships for storing ticks, daily bars, and fundamental metrics.
4. CONFIGURATION: Confirm the existence of a Configuration-Driven Architecture (e.g., config.py) decoupling the DB schema from the execution layer.

Respond in JSON: {"status": "PASSED/FAILED", "score": 0-100, "findings": [], "missing_elements": []}
