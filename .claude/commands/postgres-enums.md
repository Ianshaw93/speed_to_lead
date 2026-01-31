# PostgreSQL Enums with SQLAlchemy/Alembic

Best practices for handling PostgreSQL enums in this project.

## Key Learnings

### 1. Use `values_callable` for Python Enums

When using `str, enum.Enum` with SQLAlchemy, you MUST use `values_callable` to send the lowercase values instead of uppercase Python enum names:

```python
class DraftStatus(str, enum.Enum):
    PENDING = "pending"      # PostgreSQL expects "pending"
    APPROVED = "approved"

# WRONG - sends "PENDING" to PostgreSQL
status = mapped_column(Enum(DraftStatus, name="draft_status"))

# CORRECT - sends "pending" to PostgreSQL
status = mapped_column(
    Enum(
        DraftStatus,
        name="draft_status",
        values_callable=lambda x: [e.value for e in x],
    ),
)
```

### 2. Adding New Enum Values (Migration)

PostgreSQL requires `ALTER TYPE ... ADD VALUE` outside transaction blocks:

```python
def upgrade() -> None:
    op.execute("COMMIT")  # Exit current transaction
    op.execute("ALTER TYPE draft_status ADD VALUE 'new_status'")
```

For PostgreSQL 12+, you can use `ALTER TYPE ... ADD VALUE IF NOT EXISTS`.

### 3. Check Before Creating Types

In migrations, always check if enum types exist before creating:

```python
conn = op.get_bind()
result = conn.execute(sa.text(
    "SELECT typname FROM pg_type WHERE typname = 'draft_status'"
))
if not result.fetchone():
    conn.execute(sa.text(
        "CREATE TYPE draft_status AS ENUM ('pending', 'approved', 'rejected')"
    ))
```

### 4. Removing/Modifying Enum Values

PostgreSQL doesn't allow deleting enum values. You must:
1. Create new enum type with desired values
2. Alter columns to use new type
3. Drop old type

Consider using `alembic-enums` package to simplify this.

### 5. Alternative: Avoid Native Enums

For simpler migrations, use String columns with application-level validation:

```python
# Uses VARCHAR instead of native ENUM
status = mapped_column(String(20), default="pending")
```

Trade-off: Less DB-level enforcement but easier migrations.

## References

- [alembic-enums](https://pypi.org/project/alembic-enums/) - Simplifies enum migrations
- [alembic-postgresql-enum](https://pypi.org/project/alembic-postgresql-enum/) - Auto-detects enum changes
