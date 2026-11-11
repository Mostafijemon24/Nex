"""Add provider fields, verification runs, and monitor schedules.

001 creates tables from the current models on a fresh database, so this
revision only adds objects that are still missing.
"""

from alembic import op
import sqlalchemy as sa

revision = "002_provider_verification"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "ai_providers" in tables:
        columns = {column["name"] for column in inspector.get_columns("ai_providers")}
        additions = {
            "display_name": sa.Column("display_name", sa.String(200), nullable=False, server_default=""),
            "error_category": sa.Column("error_category", sa.String(64), nullable=False, server_default=""),
            "allow_paid_fallback": sa.Column("allow_paid_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
            "fallback_provider_id": sa.Column("fallback_provider_id", sa.String(36), nullable=False, server_default=""),
            "capabilities": sa.Column("capabilities", sa.JSON(), nullable=True),
            "discovered_models": sa.Column("discovered_models", sa.JSON(), nullable=True),
        }
        for name, column in additions.items():
            if name not in columns:
                op.add_column("ai_providers", column)
    if "verification_runs" not in tables:
        op.create_table(
            "verification_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("source", sa.String(64), nullable=False),
            sa.Column("database_name", sa.String(128), nullable=False, server_default=""),
            sa.Column("code_revision", sa.String(80), nullable=False, server_default=""),
            sa.Column("environment", sa.String(120), nullable=False, server_default=""),
            sa.Column("summary", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "monitor_schedules" not in tables:
        op.create_table(
            "monitor_schedules",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_status", sa.String(64), nullable=False, server_default=""),
            sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", name="uq_monitor_project"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "monitor_schedules" in tables:
        op.drop_table("monitor_schedules")
    if "verification_runs" in tables:
        op.drop_table("verification_runs")
