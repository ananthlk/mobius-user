"""Initial user schema for mobius_user database.

Creates: tenant, role, app_user, auth_provider_link, user_session,
         activity, user_activity, user_preference
Seeds: default activities

Revision ID: 001_initial
Revises: None
Create Date: 2025-02-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenant",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_table(
        "role",
        sa.Column("role_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_table(
        "app_user",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenant.tenant_id"), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("role.role_id"), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("first_name", sa.String(100), nullable=True),
        sa.Column("preferred_name", sa.String(100), nullable=True),
        sa.Column("timezone", sa.String(50), nullable=True, server_default="America/New_York"),
        sa.Column("locale", sa.String(10), nullable=True, server_default="en-US"),
        sa.Column("onboarding_completed_at", sa.DateTime(), nullable=True),
        sa.Column("avatar_url", sa.String(500), nullable=True),
    )
    op.create_index("ix_app_user_email", "app_user", ["email"])
    op.create_index("ix_app_user_tenant_id", "app_user", ["tenant_id"])

    op.create_table(
        "auth_provider_link",
        sa.Column("link_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("app_user.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("provider_user_id", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_auth_provider_link_user_id", "auth_provider_link", ["user_id"])
    op.create_index("ix_auth_provider_link_email", "auth_provider_link", ["email"])

    op.create_table(
        "user_session",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("app_user.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(255), nullable=True),
        sa.Column("device_info", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_user_session_user_id", "user_session", ["user_id"])
    op.create_index("ix_user_session_expires_at", "user_session", ["expires_at"])

    op.create_table(
        "activity",
        sa.Column("activity_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("activity_code", sa.String(50), unique=True, nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quick_actions", postgresql.JSONB, nullable=True),
        sa.Column("relevant_data_fields", postgresql.JSONB, nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_table(
        "user_activity",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("app_user.user_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("activity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("activity.activity_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("added_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_user_activity_user_id", "user_activity", ["user_id"])

    op.create_table(
        "user_preference",
        sa.Column("preference_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("app_user.user_id"), nullable=False, unique=True),
        sa.Column("always_require_oversight", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notification_preferences", postgresql.JSONB, nullable=True),
        sa.Column("tone", sa.String(20), nullable=True, server_default="professional"),
        sa.Column("greeting_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("ai_experience_level", sa.String(20), nullable=True, server_default="beginner"),
        sa.Column("autonomy_routine_tasks", sa.String(20), nullable=True, server_default="confirm_first"),
        sa.Column("autonomy_sensitive_tasks", sa.String(20), nullable=True, server_default="confirm_first"),
        sa.Column("display_preferences_json", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
    )

    # Seed default activities
    op.execute("""
        INSERT INTO activity (activity_id, activity_code, label, description, quick_actions, relevant_data_fields, display_order, is_active)
        VALUES
        (gen_random_uuid(), 'schedule_appointments', 'Schedule appointments',
         'Scheduling and managing patient appointments',
         '["find_available_slot", "reschedule", "cancel_appointment"]'::jsonb,
         '["appointment_status", "provider_availability"]'::jsonb, 1, true),
        (gen_random_uuid(), 'check_in_patients', 'Check in patients',
         'Patient check-in and arrival processing',
         '["verify_demographics", "collect_copay", "update_insurance"]'::jsonb,
         '["arrival_status", "copay_amount", "insurance_on_file"]'::jsonb, 2, true),
        (gen_random_uuid(), 'verify_eligibility', 'Verify eligibility',
         'Insurance eligibility verification',
         '["run_eligibility_check", "update_coverage", "flag_issue"]'::jsonb,
         '["eligibility_status", "coverage_dates", "benefit_details"]'::jsonb, 3, true),
        (gen_random_uuid(), 'submit_claims', 'Submit claims',
         'Medical claims submission and tracking',
         '["submit_claim", "check_status", "view_remittance"]'::jsonb,
         '["claim_status", "billing_codes", "expected_payment"]'::jsonb, 4, true),
        (gen_random_uuid(), 'rework_denials', 'Rework denied claims',
         'Handling claim denials and appeals',
         '["view_denial_reason", "appeal_claim", "correct_and_resubmit"]'::jsonb,
         '["denial_code", "appeal_deadline", "correction_needed"]'::jsonb, 5, true),
        (gen_random_uuid(), 'prior_authorization', 'Handle prior authorizations',
         'Prior authorization requests and tracking',
         '["submit_auth_request", "check_auth_status", "upload_clinical"]'::jsonb,
         '["auth_status", "auth_number", "expiration_date"]'::jsonb, 6, true),
        (gen_random_uuid(), 'patient_outreach', 'Patient outreach',
         'Patient communication and follow-up',
         '["send_reminder", "log_call", "schedule_callback"]'::jsonb,
         '["contact_history", "preferred_contact", "callback_notes"]'::jsonb, 7, true),
        (gen_random_uuid(), 'document_notes', 'Document clinical notes',
         'Clinical documentation and notes',
         '["add_note", "view_history", "flag_for_review"]'::jsonb,
         '["note_status", "last_updated", "provider_signature"]'::jsonb, 8, true),
        (gen_random_uuid(), 'coordinate_referrals', 'Coordinate referrals',
         'Managing patient referrals to specialists',
         '["create_referral", "check_referral_status", "upload_documents"]'::jsonb,
         '["referral_status", "specialist_info", "appointment_date"]'::jsonb, 9, true)
    """)


def downgrade() -> None:
    op.drop_table("user_preference")
    op.drop_table("user_activity")
    op.drop_table("activity")
    op.drop_table("user_session")
    op.drop_table("auth_provider_link")
    op.drop_table("app_user")
    op.drop_table("role")
    op.drop_table("tenant")
