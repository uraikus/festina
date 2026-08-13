"""SQLite schema synchronization -- claude.md #28, #30, #31, #46.

Pure planning logic: given a table's declared Festina columns and the
columns SQLite currently has (or None if the table doesn't exist yet),
compute what needs to change. No I/O -- the caller is responsible for
actually creating/opening festina.sqlite and running the resulting plan.
"""

# claude.md #30
TYPE_MAP = {
    "int": "INTEGER",
    "float": "REAL",
    "bool": "INTEGER",
    "text": "TEXT",
    "blob": "BLOB",
}


class SchemaSyncPlan:
    def __init__(self, create=False, add_columns=None, drop_columns=None, alter_columns=None):
        self.create = create
        self.add_columns = dict(add_columns) if add_columns else {}
        self.drop_columns = list(drop_columns) if drop_columns else []
        self.alter_columns = dict(alter_columns) if alter_columns else {}

    def __repr__(self):
        return (
            f"SchemaSyncPlan(create={self.create}, add_columns={self.add_columns}, "
            f"drop_columns={self.drop_columns}, alter_columns={self.alter_columns})"
        )

    def __eq__(self, other):
        if not isinstance(other, SchemaSyncPlan):
            return NotImplemented
        return (
            self.create == other.create
            and self.add_columns == other.add_columns
            and self.drop_columns == other.drop_columns
            and self.alter_columns == other.alter_columns
        )


def plan_sync(declared, existing):
    """`declared`: {column_name: festina_type_name}.
    `existing`: {column_name: sqlite_type_name} or None if the table
    doesn't exist in festina.sqlite yet.
    """
    if existing is None:
        return SchemaSyncPlan(create=True)

    add_columns = {}
    alter_columns = {}
    for name, festina_type in declared.items():
        expected_sql_type = TYPE_MAP[festina_type]
        if name not in existing:
            add_columns[name] = festina_type
        elif existing[name] != expected_sql_type:
            alter_columns[name] = festina_type

    drop_columns = [name for name in existing if name not in declared]

    return SchemaSyncPlan(create=False, add_columns=add_columns,
                           drop_columns=drop_columns, alter_columns=alter_columns)


def create_table_ddl(table_name, declared):
    columns = ", ".join(f"{name} {TYPE_MAP[t]}" for name, t in declared.items())
    return f"CREATE TABLE IF NOT EXISTS {table_name} ({columns});"


def sync_ddl(table_name, plan):
    """Best-effort DDL for a plan -- SQLite can't drop/alter columns
    directly on older versions, so callers may need the
    temporary-table-and-migrate strategy claude.md #28/#31 describe
    instead of running this verbatim."""
    statements = []
    if plan.create:
        return statements
    for name, festina_type in plan.add_columns.items():
        statements.append(f"ALTER TABLE {table_name} ADD COLUMN {name} {TYPE_MAP[festina_type]};")
    for name in plan.drop_columns:
        statements.append(f"ALTER TABLE {table_name} DROP COLUMN {name};")
    for name, festina_type in plan.alter_columns.items():
        statements.append(f"-- {table_name}.{name} requires a table rebuild to become {TYPE_MAP[festina_type]}")
    return statements
